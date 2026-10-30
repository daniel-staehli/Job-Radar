"""Explicit employer-feed refreshes and a local review queue, with no AI calls.

Original adapters based on the public schemas (no upstream code copied):
https://docs.greenhouse.io/job-board.html
https://developers.ashbyhq.com/docs/public-job-posting-api

Feed records never enter the application database until import_candidate is
called. Refreshing an empty/unavailable board never closes tracked applications.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from threading import RLock
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .db import JobDatabase, utc_now
from .manual_import import import_manual_data
from .normalize import canonicalize_url


_LOCK = RLock()
MAX_PRESETS = 50
MAX_FEED_BYTES = 10 * 1024 * 1024
MAX_FEED_JOBS = 5000
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_BOARD_HOSTS = {
    "ashby": {"jobs.ashbyhq.com"},
    "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _digest(*values: str) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]


def _board(provider: str, value: object) -> str:
    if provider not in _BOARD_HOSTS:
        raise ValueError("Choose Ashby or Greenhouse as the employer board provider.")
    board = _text(value)
    if "://" in board:
        parts = urlsplit(board)
        if (parts.scheme != "https" or parts.hostname not in _BOARD_HOSTS[provider]
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError("Use the employer's HTTPS Ashby or Greenhouse board URL, or its board name.")
        segments = parts.path.strip("/").split("/")
        board = segments[0]
    if not _SLUG.fullmatch(board):
        raise ValueError("Board name must contain only letters, numbers, hyphens or underscores (up to 100 characters).")
    return board


def _preset(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Employer preset must be a mapping.")
    provider = _text(raw.get("provider")).lower()
    board = _board(provider, raw.get("board"))
    company = _text(raw.get("company"))
    if not company or len(company) > 200:
        raise ValueError("Enter an employer name of up to 200 characters.")
    identity = "board_" + _digest(provider, board)
    if raw.get("id") not in (None, "", identity):
        raise ValueError("A preset's provider and board cannot be changed in place; remove it and add the new board.")
    return {"id": identity, "company": company, "provider": provider, "board": board}


def suggested_presets(jobs: list[dict]) -> list[dict]:
    """Suggest boards from saved vacancy URLs; do not save or fetch them."""
    found = {}
    for job in jobs:
        for field in ('source_url', 'apply_url'):
            value = job.get(field)
            if not isinstance(value, str):
                continue
            try:
                host = urlsplit(value).hostname
                provider = next((key for key, hosts in _BOARD_HOSTS.items() if host in hosts), None)
                if provider is None:
                    continue
                preset = _preset({'company': job.get('company'), 'provider': provider, 'board': value})
            except ValueError:
                continue
            found.setdefault(preset['id'], preset)
    return sorted(found.values(), key=lambda row: (row['company'].casefold(), row['provider']))


def _read(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError(f"The local discovery file is too large: {path.name}")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"The local discovery file is unreadable; it was not overwritten: {path.name}") from exc
    if not isinstance(result, dict) or result.get("version") != 1:
        raise ValueError(f"Unsupported discovery file format: {path.name}")
    return result


def _write(path: Path, value: dict) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if len(serialized.encode("utf-8")) > 50 * 1024 * 1024:
        raise ValueError("The local discovery cache is full. Remove unused employer boards before refreshing.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def list_presets(root: Path) -> list[dict]:
    """Read saved boards without fetching anything or creating local files."""
    with _LOCK:
        document = _read(Path(root) / "data/employer_presets.json", {"version": 1, "presets": []})
        rows = document.get("presets")
        if not isinstance(rows, list) or len(rows) > MAX_PRESETS:
            raise ValueError("The saved employer presets are invalid.")
        result = [_preset(row) for row in rows]
        if len({row["id"] for row in result}) != len(result):
            raise ValueError("The saved employer presets contain duplicate boards.")
        return sorted(result, key=lambda row: (row["company"].casefold(), row["provider"]))


def save_preset(root: Path, raw: dict) -> dict:
    """Add/update a board label. This only saves configuration, with no network."""
    preset = _preset(raw)
    with _LOCK:
        rows = {row["id"]: row for row in list_presets(root)}
        rows[preset["id"]] = preset
        if len(rows) > MAX_PRESETS:
            raise ValueError(f"Save up to {MAX_PRESETS} employer boards.")
        _write(Path(root) / "data/employer_presets.json", {"version": 1, "presets": list(rows.values())})
    return preset


def remove_preset(root: Path, preset_id: str) -> None:
    """Stop showing a board; tracked jobs and application history are untouched."""
    with _LOCK:
        rows = [row for row in list_presets(root) if row["id"] != preset_id]
        cached = _cache(root)
        existed = cached["boards"].pop(preset_id, None) is not None
        _write(Path(root) / "data/employer_presets.json", {"version": 1, "presets": rows})
        if existed:
            _write(Path(root) / "data/discovery_cache.json", cached)


def _cache(root: Path) -> dict:
    document = _read(Path(root) / "data/discovery_cache.json", {"version": 1, "boards": {}})
    if not isinstance(document.get("boards"), dict):
        raise ValueError("The local discovery cache has an invalid board list.")
    for state in document["boards"].values():
        if (not isinstance(state, dict) or not isinstance(state.get("candidates"), list)
                or any(not isinstance(item, dict) for item in state["candidates"])):
            raise ValueError("The local discovery cache contains invalid candidates.")
    return document


def list_candidates(root: Path) -> list[dict]:
    """Return cached review candidates, including stale copies after fetch errors."""
    with _LOCK:
        boards = _cache(root)["boards"]
        result = []
        for preset in list_presets(root):
            state = boards.get(preset["id"], {})
            for candidate in state.get("candidates", []):
                result.append({**candidate, "company": preset["company"],
                               "stale": bool(state.get("error")),
                               "refresh_error": state.get("error")})
        return sorted(result, key=lambda row: (_text(row.get("company")).casefold(),
                                              _text(row.get("title")).casefold(), row.get("id", "")))


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "li", "br", "div", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value: object) -> str:
    parser = _PlainText()
    parser.feed(html.unescape(_text(value)))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _url(value: object) -> str:
    url = _text(value)
    try:
        parts = urlsplit(url)
        if (parts.scheme in ("http", "https") and parts.hostname and not parts.username
                and not parts.password and len(url) <= 4096):
            return url
    except ValueError:
        pass
    return ""


def _candidate(preset: dict, row: dict, fetched_at: str) -> dict | None:
    if not isinstance(row, dict):
        raise ValueError("The employer feed contains a malformed posting.")
    provider = preset["provider"]
    if provider == "ashby":
        # Missing visibility is not permission to surface an unlisted posting.
        if row.get("isListed") is not True:
            return None
        source_url = _url(row.get("jobUrl"))
        if not source_url:
            raise ValueError("An Ashby posting has no valid published job URL.")
        external_id = canonicalize_url(source_url)
        location = _text(row.get("location"))
        description = _text(row.get("descriptionPlain")) or _plain(row.get("descriptionHtml"))
        work_mode = {"OnSite": "onsite", "Remote": "remote", "Hybrid": "hybrid"}.get(_text(row.get("workplaceType")), "unknown")
        country = None
        address = row.get("address")
        if isinstance(address, dict) and isinstance(address.get("postalAddress"), dict):
            country = _text(address["postalAddress"].get("addressCountry")) or None
        secondary = row.get("secondaryLocations") or []
        secondary_locations = [_text(item.get("location")) for item in secondary if isinstance(item, dict)] if isinstance(secondary, list) else []
        extras = {"apply_url": _url(row.get("applyUrl")) or None, "work_mode": work_mode,
                  "employment_type": _text(row.get("employmentType")) or None,
                  "posted_date": _text(row.get("publishedAt")).split("T")[0] or None,
                  "country": country, "secondary_locations": [v for v in secondary_locations if v]}
    else:
        external_id = str(row.get("id", ""))
        if not re.fullmatch(r"[0-9]+", external_id):
            raise ValueError("A Greenhouse posting has no valid posting ID.")
        source_url = _url(row.get("absolute_url"))
        if not source_url:
            raise ValueError("A Greenhouse posting has no valid published job URL.")
        location = _text(row.get("location", {}).get("name")) if isinstance(row.get("location"), dict) else ""
        description = _plain(row.get("content"))
        # updated_at is not a publication date; absent information stays unknown.
        extras = {"apply_url": None, "work_mode": "unknown", "employment_type": None,
                  "posted_date": None, "country": None, "secondary_locations": []}
    candidate = {"id": "candidate_" + _digest(provider, preset["board"], external_id),
                 "preset_id": preset["id"], "provider": provider, "board": preset["board"],
                 "external_id": external_id, "company": preset["company"], "title": _text(row.get("title")),
                 "location": location, "description": description, "source_url": source_url,
                 "fetched_at": fetched_at, "stale": False, **extras}
    candidate["missing_fields"] = [key for key in ("title", "location", "description") if not candidate[key]]
    candidate["importable"] = not candidate["missing_fields"]
    return candidate


def _fetch(preset: dict) -> dict:
    board = preset["board"]
    url = (f"https://api.ashbyhq.com/posting-api/job-board/{board}" if preset["provider"] == "ashby"
           else f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    started = time.monotonic()
    # Fixed public API hosts, no credentials, no redirect following or page scripts.
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=10.0), follow_redirects=False,
                      trust_env=False, headers={"Accept": "application/json", "User-Agent": "Job-Radar"}) as client:
        with client.stream("GET", url) as response:
            if response.status_code == 429:
                raise ValueError("The employer feed rate limit was reached. Wait and refresh again later.")
            if 300 <= response.status_code < 400:
                raise ValueError("The employer feed redirected unexpectedly; check the board name.")
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if time.monotonic() - started > 25:
                    raise ValueError("The employer feed took too long; refresh again later.")
                chunks.extend(chunk)
                if len(chunks) > MAX_FEED_BYTES:
                    raise ValueError("The employer feed exceeds the supported download size.")
    try:
        result = json.loads(chunks)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("The employer feed did not return valid JSON.") from exc
    if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
        raise ValueError("The employer feed has an unsupported format (no jobs list).")
    if len(result["jobs"]) > MAX_FEED_JOBS:
        raise ValueError("The employer feed contains too many postings for one refresh.")
    return result


def refresh_presets(root: Path, preset_ids: list[str] | None = None) -> dict:
    """Explicitly fetch selected boards. Failed boards retain their prior cache."""
    with _LOCK:
        presets = list_presets(root)
        if preset_ids is not None:
            unknown = set(preset_ids) - {preset["id"] for preset in presets}
            if unknown:
                raise ValueError("One or more selected employer boards no longer exist.")
            presets = [preset for preset in presets if preset["id"] in preset_ids]
        cached = _cache(root)
        errors = []
        for preset in presets:
            stamp = utc_now()
            previous = cached["boards"].get(preset["id"], {"candidates": []})
            try:
                payload = _fetch(preset)
                candidates = [_candidate(preset, row, stamp) for row in payload["jobs"]]
                candidates = [candidate for candidate in candidates if candidate is not None]
                if len({candidate["id"] for candidate in candidates}) != len(candidates):
                    raise ValueError("The employer feed contains duplicate posting identities; refresh again later.")
                state = {"candidates": candidates, "last_success_at": stamp,
                         "last_attempt_at": stamp, "error": None}
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    message = f"Employer feed returned HTTP {exc.response.status_code}; check the board name or retry later."
                elif isinstance(exc, httpx.HTTPError):
                    message = "Could not connect to the employer feed. Check your connection and retry later."
                else:
                    message = str(exc)
                state = {**previous, "last_attempt_at": stamp, "error": message}
                errors.append({"preset_id": preset["id"], "company": preset["company"], "message": message})
            cached["boards"][preset["id"]] = state
            # Save after each board so an interrupted batch preserves completed work.
            _write(Path(root) / "data/discovery_cache.json", cached)
        return {"candidates": list_candidates(root), "errors": errors, "refreshed_at": utc_now()}


def import_candidate(db: JobDatabase, candidate: dict) -> tuple[str, str]:
    """Import one explicitly selected posting, retaining external identity.

    Same-title requisitions remain separate. If a URL already belongs to a
    manual/other record, leave it untouched and ask the user to review that job.
    """
    if not isinstance(candidate, dict):
        raise ValueError("Choose a cached vacancy to import.")
    preset = _preset({key: candidate.get(key) for key in ("company", "provider", "board")})
    external_id = _text(candidate.get("external_id"))
    if not external_id or len(external_id) > 4096:
        raise ValueError("This candidate has no valid external posting identity. Refresh the board.")
    expected = _digest(preset["provider"], preset["board"], external_id)
    if candidate.get("id") != "candidate_" + expected or candidate.get("preset_id") != preset["id"]:
        raise ValueError("This candidate's identity does not match its board. Refresh the board.")
    source_url = _url(candidate.get("source_url"))
    if not source_url or (preset["provider"] == "ashby" and canonicalize_url(source_url) != external_id):
        raise ValueError("This candidate has no valid source URL or posting identity. Refresh the board.")
    if preset["provider"] == "greenhouse" and not re.fullmatch(r"[0-9]+", external_id):
        raise ValueError("This candidate has no valid Greenhouse posting ID.")
    missing = [key for key in ("title", "location", "description") if not _text(candidate.get(key))]
    if missing:
        raise ValueError("This feed omitted " + ", ".join(missing) + ". Use Review in manual form to supply the missing information.")
    raw = {key: candidate[key] for key in ("company", "title", "location", "description", "source_url")}
    # Only supplied data updates optional fields; unknown feed values do not
    # erase a country, work arrangement or publication date entered manually.
    for key in ("country", "apply_url", "employment_type", "posted_date", "role_family"):
        if candidate.get(key):
            raw[key] = candidate[key]
    if candidate.get("work_mode") in ("onsite", "hybrid", "remote"):
        raw["work_mode"] = candidate["work_mode"]
    raw["evidence"] = [f"Published employer feed: {preset['provider']}; board: {preset['board']}; posting: {external_id}"]
    if candidate.get("secondary_locations"):
        raw["evidence"].append("Other published locations: " + "; ".join(map(str, candidate["secondary_locations"])))
    return import_manual_data(db, raw, source=preset["provider"].title(), identity_key="job_ext_" + expected)
