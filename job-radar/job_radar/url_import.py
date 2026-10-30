"""Prepare a reviewable vacancy file from public pages, without AI or a profile.

The result is a draft, never an application or a database mutation. Structured
JobPosting data and public ATS feeds are preferred to page text.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import yaml

from .normalize import canonicalize_url, slugify

MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 4
PAGE_DATA_WARNING = "No JobPosting structured data was found. Review the extracted page text and fill any blank fields."
PAGE_DESCRIPTION_WARNING = "The page may require JavaScript or browser access. Paste the full vacancy description into the draft."
REQUIRED_FIELDS = ("company", "title", "location", "source_url", "description")
OVERRIDE_FIELDS = set(REQUIRED_FIELDS) | {
    "country", "work_mode", "employment_type", "posted_date", "deadline",
    "apply_url", "source", "domain", "role_family", "salary_text",
}


class FetchError(ValueError):
    """A public page could not safely be fetched."""


def validate_public_url(url: str, *, resolve: bool = False) -> tuple[str, list[str]]:
    """Validate a URL, optionally resolving and checking every destination IP."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Provide an http:// or https:// vacancy link.")
    url = url.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("The vacancy link contains invalid characters.")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("The vacancy link is not a valid URL.") from exc
    if parts.scheme not in {"https", "http"} or not host or parts.username is not None or parts.password is not None:
        raise ValueError("Use a public http:// or https:// link without login credentials.")
    if port not in (None, 443 if parts.scheme == "https" else 80):
        raise ValueError("Only standard public web ports (HTTP 80 / HTTPS 443) are supported.")
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host and ":" not in host:
        raise ValueError("Local or private network links are not supported.")
    addresses = []
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if resolve:
            try:
                addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(
                    host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
                )))
            except OSError as exc:
                raise FetchError("Could not resolve the vacancy website.") from exc
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Local, private and reserved network destinations are not supported.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, "")), addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def connect(self) -> None:
        raw_socket = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def fetch_public_page(url: str) -> tuple[str, str]:
    """Fetch bounded public text, checking redirects and pinning validated IPs.

    No cookies, credentials, environment proxies, JavaScript or model calls.
    Pinned connections prevent a second DNS lookup changing to a private IP.
    """
    current = url
    deadline = time.monotonic() + TIMEOUT_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        current, addresses = validate_public_url(current, resolve=True)
        if not addresses:
            raise FetchError("The vacancy website has no public network address.")
        parts = urlsplit(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("The vacancy website took too long to respond.")
        connection_type = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
        # Prefer IPv4 where available; many desktop networks advertise IPv6
        # DNS answers without providing working IPv6 connectivity.
        address = min(addresses, key=lambda value: ipaddress.ip_address(value).version)
        connection = connection_type(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), address, remaining)
        try:
            connection.request("GET", urlunsplit(("", "", parts.path or "/", parts.query, "")), headers={
                "User-Agent": "JobRadar/0.4 (manual vacancy import)",
                "Accept": "text/html,application/ld+json,application/json,text/plain;q=0.8",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = response.getheader("Location")
                if not target:
                    raise FetchError("The vacancy website returned a redirect without a destination.")
                current = urljoin(current, target)
                continue
            if response.status >= 400:
                raise FetchError(f"Website returned HTTP {response.status}. Open it in your browser and paste the vacancy text into the draft.")
            content_type = response.getheader("Content-Type", "").lower()
            if content_type and not any(value in content_type for value in ("text/", "json", "xhtml")):
                raise FetchError("This link does not return an HTML, text or JSON vacancy page.")
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise FetchError("The website returned an unsupported compressed response.")
            length = response.getheader("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise FetchError("The vacancy page exceeds the 2 MB download limit.")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("The vacancy website took too long to respond.")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError("The vacancy page exceeds the 2 MB download limit.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                return b"".join(chunks).decode(charset, errors="replace"), current
            except LookupError:
                return b"".join(chunks).decode("utf-8", errors="replace"), current
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError("Could not download the vacancy page. Open it in your browser and paste the text into the draft.") from exc
        finally:
            connection.close()
    raise FetchError("The vacancy link redirected too many times.")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _PageParser(HTMLParser):
    BLOCKS = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li", "ul", "ol", "br"}
    SKIP = {"script", "style", "noscript", "nav", "footer", "form", "svg"}
    VOID = {"meta", "link", "br", "hr", "img", "input", "source", "wbr", "area", "base", "embed", "param", "track", "col"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.jsonld = []
        self.bamboo_branding = []
        self.meta = {}
        self.props = {}
        self.h1 = []
        self.h1_count = 0
        self.title = []
        self.main = []
        self.body = []
        self._script = None
        self._script_target = None
        self.has_job_posting = False
        self.job_posting_count = 0
        self.detail_headers = []
        self.detail_descriptions = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if any(value.rstrip("/").rsplit("/", 1)[-1] == "JobPosting" for value in attrs.get("itemtype", "").split()):
            self.has_job_posting = True
            self.job_posting_count += 1
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"]
            # Microdata often stores employer and address fields in empty meta
            # elements. Include their literal content in their parent scopes,
            # e.g. streetAddress inside address inside jobLocation.
            if (attrs.get("itemprop") and attrs.get("content")
                    and not any(frame["tag"] in self.SKIP or frame["hidden"] for frame in self.stack)):
                for frame in [*self.stack, {"itemprop": attrs["itemprop"]}]:
                    for prop in frame["itemprop"].split():
                        self.props.setdefault(prop, []).extend([attrs["content"], " "])
        if tag == "h1":
            self.h1_count += 1
        if tag in self.BLOCKS:
            self._add_text("\n")
        frame = {"tag": tag, "itemprop": attrs.get("itemprop", ""), "hidden": "hidden" in attrs or attrs.get("aria-hidden") == "true"}
        # These exact, scoped markers describe a vacancy header/description;
        # unrelated page headings and labelled footer addresses are not jobs.
        classes = set(attrs.get("class", "").split())
        frame["job_details"] = "job-details" in classes
        frame["detail_hidden"] = bool(re.search(
            r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important\s*)?(?:;|$)",
            attrs.get("style", ""), re.IGNORECASE))
        visible = not any(item["tag"] in self.SKIP or item["hidden"] or item.get("detail_hidden")
                          for item in [*self.stack, frame])
        in_details = frame["job_details"] or any(item.get("job_details") for item in self.stack)
        if visible and in_details:
            if "title-card" in classes:
                header = {"text": [], "headings": []}
                frame["detail_header"] = header
                self.detail_headers.append(header)
            if "job-details__content-description" in classes:
                description = []
                frame["detail_description"] = description
                self.detail_descriptions.append(description)
            if tag in {"h1", "h2"}:
                headers = [item["detail_header"] for item in self.stack if "detail_header" in item]
                if len(headers) == 1:
                    heading = []
                    headers[0]["headings"].append(heading)
                    frame["detail_heading"] = heading
        if tag == "script":
            script_type = attrs.get("type", "").split(";", 1)[0].strip().lower()
            if script_type == "application/ld+json":
                self._script, self._script_target = [], self.jsonld
            elif script_type == "application/json" and attrs.get("id") == "poData":
                self._script, self._script_target = [], self.bamboo_branding
        if tag not in self.VOID:
            self.stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self._script_target.append("".join(self._script))
            self._script = None
            self._script_target = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break
        if tag in self.BLOCKS:
            self._add_text("\n")

    def handle_data(self, value):
        if self._script is not None:
            self._script.append(value)
            return
        self._add_text(value)

    def _add_text(self, value):
        tags = {frame["tag"] for frame in self.stack}
        if tags & self.SKIP or any(frame["hidden"] for frame in self.stack):
            return
        self.body.append(value)
        if not any(frame.get("detail_hidden") for frame in self.stack):
            for frame in self.stack:
                if "detail_header" in frame:
                    # Source formatting may wrap a label/value across lines;
                    # only actual HTML block/BR boundaries separate fields.
                    header_text = "\n" if value == "\n" else re.sub(r"\s+", " ", value)
                    frame["detail_header"]["text"].append(header_text)
                if "detail_heading" in frame:
                    frame["detail_heading"].append(value)
                if "detail_description" in frame:
                    frame["detail_description"].append(value)
        if "h1" in tags:
            self.h1.append(value)
        if "title" in tags:
            self.title.append(value)
        if tags & {"main", "article"}:
            self.main.append(value)
        for frame in self.stack:
            for prop in frame["itemprop"].split():
                self.props.setdefault(prop, []).append(value)


def html_to_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _PageParser()
    # Public ATS feeds sometimes escape the entire HTML fragment twice.
    parser.feed(unescape(unescape(value)))
    return _clean_text("".join(parser.body))


def _objects(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _name(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("name"))
    return ""


def _join_names(value: object) -> str:
    return "; ".join(filter(None, (_name(item) for item in (value if isinstance(value, list) else [value]))))


def _location(value: object) -> tuple[str, str]:
    locations, countries = [], []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            locations.append(item.strip())
        elif isinstance(item, dict):
            address = item.get("address", item)
            if isinstance(address, str):
                locations.append(address.strip())
            elif isinstance(address, dict):
                country = _name(address.get("addressCountry"))
                parts = [_text(address.get("addressLocality")), _text(address.get("addressRegion")), country]
                label = ", ".join(dict.fromkeys(filter(None, parts))) or _name(item)
                if label:
                    locations.append(label)
                if country:
                    countries.append(country)
    return "; ".join(dict.fromkeys(filter(None, locations))), "; ".join(dict.fromkeys(countries))


def _blank_job(url: str) -> dict:
    return {"company": "", "title": "", "location": "", "source_url": url, "description": "", "source": "company website"}


def _result(job: dict, method: str, warnings: list[str], final_url: str) -> dict:
    missing = [field for field in REQUIRED_FIELDS if not _text(job.get(field))]
    return {"job": job, "complete": not missing, "needs_review": True, "missing_fields": missing,
            "warnings": warnings, "extraction_method": method, "final_url": final_url}


def _from_jsonld(posting: dict, url: str) -> dict:
    job = _blank_job(url)
    job.update(company=_join_names(posting.get("hiringOrganization")), title=_text(posting.get("title")),
               description=html_to_text(posting.get("description")))
    job["location"], country = _location(posting.get("jobLocation"))
    if country:
        job["country"] = country
    if "TELECOMMUTE" in _join_names(posting.get("jobLocationType")).upper():
        job["work_mode"] = "remote"
        if not job["location"]:
            restrictions = _join_names(posting.get("applicantLocationRequirements"))
            job["location"] = "Remote" + (" — " + restrictions if restrictions else "")
    for field, schema_field in (("employment_type", "employmentType"), ("posted_date", "datePosted"), ("deadline", "validThrough")):
        value = _join_names(posting.get(schema_field))
        if value:
            job[field] = value
    return job


def _bamboo_company(parser: _PageParser) -> str:
    """Read the literal employer logo label; never execute the page's scripts."""
    names = set()
    for raw in parser.bamboo_branding:
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        for key in ("site", "logo", "alt"):
            value = value.get(key) if isinstance(value, dict) else None
        name = _text(value)
        if name and name.casefold() not in {"bamboohr", "logo", "company logo"}:
            names.add(name)
    return next(iter(names)) if len(names) == 1 else ""


def _header_labels(text: str) -> dict[str, list[str]]:
    """Read explicit label/value lines inside an already scoped vacancy header."""
    values = {"location": [], "job_id": []}
    lines = [line.strip() for line in _clean_text(text).splitlines() if line.strip()]
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(Location|Job\s+ID)\s*:\s*(.*)", line, re.IGNORECASE)
        if not match:
            continue
        name = "location" if match.group(1).casefold() == "location" else "job_id"
        value = match.group(2).strip()
        if not value and index + 1 < len(lines):
            following = lines[index + 1]
            # A missing value must not consume another label or a control.
            if not re.match(r"[^:]{1,50}:|(?:apply|share|save)\b", following, re.IGNORECASE):
                value = following
        value = " ".join(value.split())
        if value and len(value) <= 500 and value not in values[name]:
            values[name].append(value)
    return values


def _labelled_page_fallback(parser: _PageParser, url: str, page_title: str,
                            props: dict, job: dict) -> dict | None:
    """Recognise one labelled vacancy, never arbitrary body text or the first H2."""
    if not parser.detail_headers:
        return None

    def ambiguous(reason):
        return _result(_blank_job(url), "ambiguous", [reason + " Review the vacancy details manually."], url)

    if len(parser.detail_headers) != 1:
        return ambiguous("Several visible vacancy headers were found.")
    header = parser.detail_headers[0]
    labels = _header_labels("".join(header["text"]))
    if not labels["job_id"]:
        return None  # A generic title card alone is insufficient vacancy evidence.
    headings = [" ".join("".join(parts).split()) for parts in header["headings"]]
    headings = [value for value in headings if value]
    if len(headings) != 1 or len(labels["job_id"]) != 1:
        return ambiguous("The vacancy header contained several titles or job identifiers, or no title.")
    if len(labels["location"]) > 1:
        return ambiguous("The vacancy header contained conflicting locations.")
    if len(parser.detail_descriptions) > 1:
        return ambiguous("Several visible vacancy descriptions were found.")

    title = headings[0]
    # A page-wide H1 may be a site heading. Explicit properties may fill gaps,
    # but conflicting literal metadata must not be blended into one vacancy.
    explicit_title = props.get("title") or parser.meta.get("job:title") or ""
    location = labels["location"][0] if labels["location"] else ""
    explicit_location = props.get("jobLocation") or parser.meta.get("job:location") or ""
    for label, visible, explicit in (("title", title, explicit_title),
                                     ("location", location, explicit_location)):
        if visible and explicit and " ".join(visible.split()).casefold() != " ".join(explicit.split()).casefold():
            return ambiguous(f"The labelled vacancy {label} conflicted with other page metadata.")
    title = explicit_title or title
    location = explicit_location or location
    company = props.get("hiringOrganization") or parser.meta.get("job:company") or ""
    normalized_page_title = " ".join(page_title.split())
    if not company and normalized_page_title.startswith(title):
        suffix = normalized_page_title[len(title):]
        match = re.fullmatch(r"\s+(?:in\s+.+?\s+)?[-–—|]\s+(.+)", suffix)
        if match:
            candidate = match.group(1).strip()
            if candidate.casefold() not in {"careers", "jobs", "job search", "career search"}:
                company = candidate
    description = _clean_text("".join(parser.detail_descriptions[0])) if parser.detail_descriptions else ""
    result = dict(job)
    result.update(title=title, location=location, company=company,
                  description=props.get("description") or description or job.get("description", ""))
    warnings = ["Details were read from labelled vacancy-page sections. Review them before saving."]
    if not result["description"]:
        warnings.append(PAGE_DESCRIPTION_WARNING)
    return _result(result, "labelled-page", warnings, url)


def parse_job_page(html: str, url: str) -> dict:
    """Extract literal page facts. Ambiguous multi-job pages are not guessed."""
    parser = _PageParser()
    parser.feed(html)
    warnings, objects = [], []
    for raw in parser.jsonld:
        try:
            objects.extend(_objects(json.loads(raw)))
        except (ValueError, RecursionError):
            warnings.append("A structured-data block could not be read.")
    index = {obj["@id"]: obj for obj in objects if isinstance(obj.get("@id"), str) and len(obj) > 1}

    def resolve(value, depth=0):
        if depth > 12:
            return value
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value]
        if isinstance(value, dict):
            if set(value) == {"@id"} and value["@id"] in index:
                return resolve(index[value["@id"]], depth + 1)
            return {key: resolve(item, depth + 1) for key, item in value.items()}
        return value

    candidates = []
    for obj in objects:
        kinds = obj.get("@type", [])
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if any(isinstance(kind, str) and kind.rsplit("/", 1)[-1] == "JobPosting" for kind in kinds):
            candidate = resolve(obj)
            if candidate not in candidates:
                candidates.append(candidate)
    if len(candidates) > 1:
        matches = [item for item in candidates if any(
            _text(item.get(field)) and canonicalize_url(urljoin(url, item[field])) == canonicalize_url(url)
            for field in ("url", "@id")
        )]
        if len(matches) != 1:
            return _result(_blank_job(url), "ambiguous", ["This page contains several vacancies. Use the direct link to one job or fill the draft manually."], url)
        candidates = matches
    if candidates:
        return _result(_from_jsonld(candidates[0], url), "json-ld", warnings, url)
    if parser.job_posting_count > 1:
        return _result(_blank_job(url), "ambiguous", ["This page contains several vacancies. Use the direct link to one job or fill the draft manually."], url)

    job = _blank_job(url)
    page_heading = _clean_text("".join(parser.h1))
    page_title = _clean_text("".join(parser.title))
    if any(re.search(pattern, value, re.IGNORECASE) for value in (page_heading, page_title) for pattern in (
        r"\baccess denied\b", r"\bverify (?:you are|you're) human\b", r"\bjust a moment\b",
        r"\bsign in\b", r"\blog in\b", r"\bpage not found\b", r"\bjob (?:not found|no longer available)\b",
    )):
        return _result(job, "blocked-page", ["The website returned a login, verification or unavailable page. Open the vacancy in your browser and paste its text into the draft."], url)
    if re.fullmatch(r"(?:career search|job search|search jobs|open positions|current vacancies|open vacancies)", page_heading, re.IGNORECASE):
        return _result(job, "listing-page", ["The link leads to a careers listing rather than an individual vacancy. The vacancy may have closed; use a current direct link or paste the saved vacancy text into the draft."], url)
    props = {key: _clean_text("".join(value)) for key, value in parser.props.items()}
    job["title"] = props.get("title") or parser.meta.get("job:title") or (page_heading if parser.h1_count == 1 else "")
    job["company"] = props.get("hiringOrganization") or parser.meta.get("job:company") or ""
    endpoint = _ats_endpoint(url)
    if not job["company"] and endpoint and endpoint[0] == "bamboohr":
        job["company"] = _bamboo_company(parser)
    job["location"] = props.get("jobLocation") or parser.meta.get("job:location") or ""
    # Explicit title conventions used on ATS pages; never infer an employer from a domain/slug.
    match = re.fullmatch(r"(.+?)\s+(?:@|at)\s+(.+)", page_title)
    if match:
        if not job["title"]:
            job["title"] = match.group(1).strip()
        if not job["company"] and job["title"] == match.group(1).strip():
            job["company"] = match.group(2).strip()
    job["description"] = props.get("description") or _clean_text("".join(parser.main))
    if parser.has_job_posting:
        for field, prop in (("posted_date", "datePosted"), ("deadline", "validThrough"), ("employment_type", "employmentType")):
            if props.get(prop):
                job[field] = props[prop]
        return _result(job, "microdata", warnings, url)
    labelled = _labelled_page_fallback(parser, url, page_title, props, job)
    if labelled is not None:
        labelled["warnings"] = warnings + labelled["warnings"]
        return labelled
    warnings.append(PAGE_DATA_WARNING)
    if not job["description"]:
        warnings.append(PAGE_DESCRIPTION_WARNING)
    return _result(job, "page-text", warnings, url)


def _ats_endpoint(url: str) -> tuple[str, str, str] | None:
    parts = urlsplit(url)
    path = [part for part in parts.path.split("/") if part]
    if (re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.bamboohr\.com", parts.hostname or "")
            and re.fullmatch(r"/careers/[0-9]+/?", parts.path)):
        identifier = path[1]
        return "bamboohr", f"https://{parts.hostname}/careers/{identifier}/detail", identifier
    if len(path) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path):
        return None
    board, identifier = path[:2]
    if parts.hostname == "jobs.ashbyhq.com":
        return "ashby", f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}", identifier
    if parts.hostname in {"jobs.lever.co", "jobs.eu.lever.co"}:
        host = "api.eu.lever.co" if parts.hostname == "jobs.eu.lever.co" else "api.lever.co"
        return "lever", f"https://{host}/v0/postings/{quote(board)}/{quote(identifier)}", identifier
    if parts.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and len(path) == 3 and path[1] == "jobs" and path[2].isdigit():
        return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs/{path[2]}", path[2]
    return None


def _parse_ats(kind: str, raw: str, url: str, identifier: str) -> dict | None:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("The public job feed did not return an object.")
    job = _blank_job(url)
    if kind == "ashby":
        matches = [item for item in data.get("jobs", []) if isinstance(item, dict) and (
            _text(item.get("id")) == identifier or urlsplit(_text(item.get("jobUrl"))).path.rstrip("/").split("/")[-1] == identifier
        )]
        if len(matches) != 1:
            return None
        data = matches[0]
        job.update(title=_text(data.get("title")), company=_text(data.get("companyName")),
                   location=_text(data.get("location")), description=_text(data.get("descriptionPlain")) or html_to_text(data.get("descriptionHtml")))
        secondary = [item.get("location", "") for item in data.get("secondaryLocations", []) if isinstance(item, dict)]
        job["location"] = "; ".join(dict.fromkeys(filter(None, [job["location"], *map(_text, secondary)])))
        mode = _text(data.get("workplaceType")).lower()
        if mode in {"onsite", "hybrid", "remote"}:
            job["work_mode"] = mode
        elif data.get("isRemote") is True:
            job["work_mode"] = "remote"
        if not job["location"] and job.get("work_mode") == "remote":
            job["location"] = "Remote"
        job["posted_date"] = _text(data.get("publishedAt"))
        job["employment_type"] = _text(data.get("employmentType"))
        job["apply_url"] = _text(data.get("applyUrl"))
    elif kind == "lever":
        if _text(data.get("id")) != identifier:
            return None
        categories = data.get("categories") or {}
        sections = [_text(data.get("descriptionPlain")) or html_to_text(data.get("description"))]
        for section in data.get("lists", []):
            if isinstance(section, dict):
                sections.extend([_text(section.get("text")), html_to_text(section.get("content"))])
        sections.append(_text(data.get("additionalPlain")) or html_to_text(data.get("additional")))
        job.update(title=_text(data.get("text")), company=_text(data.get("companyName")),
                   location=_text(categories.get("location")), description="\n\n".join(filter(None, sections)))
        job["employment_type"] = _text(categories.get("commitment"))
        job["apply_url"] = _text(data.get("applyUrl"))
        mode = _text(data.get("workplaceType")).lower().replace("-", "")
        if mode in {"onsite", "hybrid", "remote"}:
            job["work_mode"] = mode
    elif kind == "bamboohr":
        result = data.get("result")
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        if not isinstance(opening, dict):
            return None
        # The public detail response identifies the vacancy by its share URL,
        # not by a numeric id field. Both tenant and id must match the request.
        share_url, _ = validate_public_url(_text(opening.get("jobOpeningShareUrl")))
        if _ats_endpoint(share_url) != _ats_endpoint(url):
            return None
        status = _text(opening.get("jobOpeningStatus"))
        if status and status.casefold() != "open":
            return None
        location = opening.get("location")
        if isinstance(location, dict):
            job["location"], country = _location({
                "addressLocality": location.get("city"),
                "addressRegion": location.get("state"),
                "addressCountry": location.get("addressCountry"),
            })
            if country:
                job["country"] = country
        else:
            job["location"] = _text(location)
        job.update(title=_text(opening.get("jobOpeningName")),
                   description=html_to_text(opening.get("description")),
                   employment_type=_text(opening.get("employmentStatusLabel")),
                   posted_date=_text(opening.get("datePosted")))
    else:
        if str(data.get("id")) != identifier:
            return None
        job.update(title=_text(data.get("title")), company=_text(data.get("company_name")),
                   location=_name(data.get("location")), description=html_to_text(data.get("content")))
        job["posted_date"] = _text(data.get("first_published"))
        job["deadline"] = _text(data.get("application_deadline"))
    return {key: value for key, value in job.items() if value or key in REQUIRED_FIELDS}


class _DraftDumper(yaml.SafeDumper):
    pass


def _represent_text(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)


_DraftDumper.add_representer(str, _represent_text)


def prepare_job_url(url: str, *, output_dir: Path, output_path: Path | None = None,
                    overwrite: bool = False, dry_run: bool = False,
                    overrides: dict | None = None, fetch_fn=None) -> dict:
    """Download a vacancy and save an editable YAML draft and extraction audit.

    Missing facts stay blank. `complete` means required fields are present, not
    that a human has verified them. No database or candidate files are touched.
    The optional fetch function is an injectable (url)->(text, final_url) adapter.
    """
    url, _ = validate_public_url(url)
    overrides = overrides or {}
    if not isinstance(overrides, dict) or set(overrides) - OVERRIDE_FIELDS:
        raise ValueError("Unsupported vacancy override fields.")
    if any(not isinstance(value, str) for value in overrides.values()):
        raise ValueError("Vacancy overrides must contain text values.")
    if "source_url" in overrides:
        raise ValueError("Use the link argument to choose the vacancy URL.")
    if "work_mode" in overrides and overrides["work_mode"] not in {"onsite", "hybrid", "remote", "unknown"}:
        raise ValueError("work_mode must be onsite, hybrid, remote or unknown.")
    if output_path is not None:
        output_path = Path(output_path)
        if output_path.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("Choose an output filename ending in .yaml or .yml.")
        if not overwrite and (output_path.exists() or output_path.with_suffix(".extraction.json").exists()):
            raise FileExistsError(f"Draft already exists: {output_path}. Choose another filename or explicitly overwrite it.")
    fetch = fetch_fn or fetch_public_page
    warnings, fetched, html = [], [], ""
    final_url = url
    try:
        html, final_url = fetch(url)
        validate_public_url(final_url)
        if len(html.encode("utf-8")) > MAX_BYTES:
            raise FetchError("The vacancy page exceeds the 2 MB text limit.")
        parsed = parse_job_page(html, final_url)
        fetched.append({"url": final_url, "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()})
    except (ValueError, OSError, http.client.HTTPException) as exc:
        final_url = url
        parsed = _result(_blank_job(url), "unavailable", [], url)
        warnings.append(str(exc))
    # The direct page remains the identity even when a public API provides text.
    parsed["job"]["source_url"] = url
    endpoint = _ats_endpoint(url)
    if endpoint and (not parsed["complete"] or parsed["extraction_method"] != "json-ld"):
        kind, api_url, identifier = endpoint
        try:
            raw, api_final_url = fetch(api_url)
            validate_public_url(api_final_url)
            if len(raw.encode("utf-8")) > MAX_BYTES:
                raise FetchError("The public job feed exceeds the 2 MB text limit.")
            api_job = _parse_ats(kind, raw, url, identifier)
            if api_job:
                parsed["job"].update({key: value for key, value in api_job.items() if value})
                parsed["extraction_method"] = f"{kind}-public-api"
                if all(_text(parsed["job"].get(field)) for field in REQUIRED_FIELDS):
                    parsed["warnings"] = [warning for warning in parsed["warnings"]
                                          if warning not in {PAGE_DATA_WARNING, PAGE_DESCRIPTION_WARNING}]
                fetched.append({"url": api_final_url, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()})
            else:
                warnings.append("The requested vacancy was not present in the public job feed; it may have closed.")
        except (ValueError, OSError, http.client.HTTPException, TypeError, KeyError) as exc:
            warnings.append(f"Public job feed could not supply this vacancy: {exc}")
    parsed["job"].update({key: value.strip() for key, value in overrides.items()})
    # Untrusted page URLs must not introduce local or credential-bearing links.
    if parsed["job"].get("apply_url"):
        try:
            parsed["job"]["apply_url"], _ = validate_public_url(parsed["job"]["apply_url"])
        except ValueError:
            parsed["job"].pop("apply_url")
            warnings.append("An invalid application URL was omitted.")
    result = _result(parsed["job"], parsed["extraction_method"], warnings + parsed["warnings"], final_url)
    result.update(yaml_path=None, audit_path=None)
    if dry_run:
        return result
    if output_path is None:
        stem = slugify(" ".join(filter(None, (result["job"]["company"], result["job"]["title"])))) or "vacancy"
        output_path = Path(output_dir) / f"{stem}-{uuid4().hex[:8]}.yaml"
    audit_path = output_path.with_suffix(".extraction.json")
    if not overwrite and (output_path.exists() or audit_path.exists()):
        raise FileExistsError(f"Draft already exists: {output_path}")
    audit = {"requested_url": url, "final_url": final_url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
             "extraction_method": result["extraction_method"], "complete": result["complete"], "needs_review": True,
             "missing_fields": result["missing_fields"], "warnings": result["warnings"], "sources": fetched,
             "override_fields": sorted(overrides), "model_used": False}
    comments = ["# Vacancy draft extracted from a public link. Review before importing or generating documents."]
    if result["missing_fields"]:
        comments.append("# Fill these blank fields: " + ", ".join(result["missing_fields"]))
    comments.extend("# " + warning.replace("\n", " ") for warning in result["warnings"])
    content = "\n".join(comments) + "\n" + yaml.dump(result["job"], Dumper=_DraftDumper, sort_keys=False, allow_unicode=True, width=100)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with output_path.open(mode, encoding="utf-8") as handle:
        handle.write(content)
    with audit_path.open(mode, encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    result.update(yaml_path=output_path, audit_path=audit_path)
    return result
