from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "gh_src",
    "source",
    "sourceid",
    "src",
    "ref",
    "refid",
    "trackingid",
    "trk",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def canonicalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_KEYS and not key.casefold().startswith("utm_")
    ]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), "")
    )


def identity_key(company: str, title: str, location: str) -> str:
    return "|".join(
        (normalized_text(company), normalized_text(title), normalized_text(location))
    )


def stable_job_id(company: str, title: str, location: str) -> str:
    digest = hashlib.sha256(identity_key(company, title, location).encode()).hexdigest()
    return f"job_{digest[:12]}"


def payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def slugify(value: str, max_length: int = 70) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_text(value)).strip("-")
    return slug[:max_length].rstrip("-")

