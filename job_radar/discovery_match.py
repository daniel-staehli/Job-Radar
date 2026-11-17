"""Explicit, bounded Ollama reviews of fetched vacancies; no tracker mutations."""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit

from . import discovery, job_match
from .cancellation import GenerationCancelled, check_cancelled
from .normalize import canonicalize_url
from .providers import normalize_settings, provider_identity

MAX_BATCH = 10
CATEGORIES = ("strong", "plausible", "stretch", "poor", "insufficient_evidence")


def local_settings(settings: dict) -> dict:
    value = normalize_settings(settings, require_model=True)
    host = urlsplit(value["base_url"]).hostname
    if value["provider"] != "ollama" or host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Choose Ollama (local) and an address on this computer in AI settings before reviewing Find jobs results.")
    # Ollama may proxy a cloud model even on localhost. Do not promise local
    # inference for model names that explicitly request the cloud service.
    if "cloud" in value["model"].casefold():
        raise ValueError("Choose a locally installed Ollama model, not a cloud model, for this local review.")
    return value


def candidate_job(candidate: dict) -> dict:
    preset = discovery._preset({key: candidate.get(key) for key in ("company", "provider", "board")})
    external = str(candidate.get("external_id", ""))
    identity = discovery._digest(preset["provider"], preset["board"], external)
    if (not external or candidate.get("id") != "candidate_" + identity
            or candidate.get("preset_id") != preset["id"]):
        raise ValueError("This vacancy's identity has changed. Refresh vacancies first.")
    url = discovery._url(candidate.get("source_url"))
    if not url or (preset["provider"] == "ashby" and canonicalize_url(url) != external):
        raise ValueError("This vacancy has an invalid source link. Refresh vacancies first.")
    if not candidate.get("title"):
        raise ValueError("This vacancy has no job title. Complete it in the manual form first.")
    # Same stable identity as Save selected job: a review remains available after
    # the user saves the vacancy, without creating a database record here.
    return {**candidate, "job_id": "job_ext_" + identity}


def saved_reviews(root, candidates: list[dict]) -> dict:
    try:
        profile_hash = job_match._hash(job_match._profile_snapshot(root))
    except ValueError:
        profile_hash = "unavailable"
    result = {}
    for candidate in candidates:
        try:
            review = job_match.latest_assessment(root, candidate_job(candidate), profile_hash=profile_hash)
            if review:
                result[candidate["id"]] = {**review, "stale": review["stale"] or bool(candidate.get("stale"))}
        except (ValueError, OSError):
            # Do not break vacancy discovery because a previous AI review is bad.
            result[candidate["id"]] = {"error": "Saved review unavailable; run a new review."}
    return result


def label(review: dict | None) -> str:
    if not review:
        return "Not reviewed"
    if review.get("error"):
        return "Unavailable"
    if review.get("stale"):
        return "Outdated"
    return review["assessment"]["category"].replace("_", " ").capitalize()


def rank(review: dict | None) -> int:
    if not review or review.get("error") or review.get("stale"):
        return len(CATEGORIES)
    return CATEGORIES.index(review["assessment"]["category"])


def review_candidates(root, candidates: list[dict], settings: dict, *, reuse=True, progress=None) -> dict:
    """One request per selected vacancy; preserve successes on stop or failure."""
    selected = deepcopy(candidates)
    if not 1 <= len(selected) <= MAX_BATCH:
        raise ValueError(f"Choose between 1 and {MAX_BATCH} vacancies per review.")
    if len({item["id"] for item in selected}) != len(selected):
        raise ValueError("Each selected vacancy must appear only once.")
    settings = local_settings(settings)
    identity = provider_identity(settings)
    # Validate the profile and every selected input before the first model call.
    profile_hash = job_match._hash(job_match._profile_snapshot(root))
    jobs = [candidate_job(item) for item in selected]
    for item, job in zip(selected, jobs):
        if item.get("stale"):
            raise ValueError("Some selected vacancies are from a failed refresh. Refresh successfully or exclude them first.")
        job_match._job_snapshot(job)
    previous = saved_reviews(root, selected) if reuse else {}
    result = {"reviewed": [], "reused": [], "errors": [], "cancelled": False}
    for index, (candidate, job) in enumerate(zip(selected, jobs), 1):
        try:
            check_cancelled()
            if job_match._hash(job_match._profile_snapshot(root)) != profile_hash:
                raise ValueError("Your saved profile changed during the batch. Run a fresh review for the remaining jobs.")
            prior = previous.get(candidate["id"])
            if (prior and not prior.get("error") and not prior.get("stale")
                    and prior.get("provider") == identity):
                result["reused"].append(candidate["id"])
                if progress:
                    progress(f"{index}/{len(selected)}: reused saved review for {candidate['title']}")
                continue
            if progress:
                progress(f"{index}/{len(selected)}: reviewing {candidate['company']} — {candidate['title']}")
            job_match.assess_job(root, job, settings)
            result["reviewed"].append(candidate["id"])
        except GenerationCancelled:
            result["cancelled"] = True
            break
        except Exception as exc:
            result["errors"].append({"id": candidate["id"], "title": candidate["title"], "message": str(exc)})
            # No retries or cascade of requests after a provider/validation error.
            break
    return result
