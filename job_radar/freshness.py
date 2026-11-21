"""Publication age and observation history for saved employer boards."""
from datetime import datetime, timezone
import hashlib
import re


DEFAULT_HOURS = 48
FILTERS = ("All ages", "Fresh", "Programs", "Date unknown", "Older")
_PROGRAM = re.compile(
    r"\b(?:interns?|internships?|trainees?(?:ships?)?|graduate (?:development |training |rotation |rotational )?(?:program(?:me)?s?|schemes?)|"
    r"(?:spontaneous|speculative|unsolicited|open) applications?|praktik(?:um|ant\w*)|"
    r"initiativbewerbung\w*|stagiaire\w*|stage|candidatures? spontanées?)\b", re.I)


def instant(value):
    """Accept offset-aware timestamps and ISO dates (midnight UTC)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return None
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def publication(row):
    return instant(row.get("published_at") or row.get("posted_date"))


def category(row, hours, now):
    if any(_PROGRAM.search(str(row.get(key) or "")) for key in ("title", "employment_type")):
        return "Programs"
    published = publication(row)
    if published is None or published > now:
        return "Date unknown"
    if (now - published).total_seconds() > hours * 3600:
        return "Older"
    if row.get("possible_repost") or re.search(r"\bre-?post(?:ed|ing)?\b", row.get("title") or "", re.I):
        return "Date unknown"
    return "Fresh"


def _fingerprint(row):
    fields = [" ".join(str(row.get(k) or "").casefold().split()) for k in ("title", "location", "description")]
    if all(fields):
        return hashlib.sha256("\0".join(fields).encode()).hexdigest()


def _original_publication(records, first_seen):
    # A timestamp can restore the hour omitted by an older date-only cache.
    # For repeated timestamps on the same day, keep the earliest one.
    by_day = {}
    for record in records:
        raw = record.get("published_at") or record.get("posted_date")
        when = instant(raw)
        if when is None or when > first_seen:
            continue
        day = when.date().isoformat()
        times = by_day.setdefault(day, [])
        if len(raw) > 10:
            times.append(when)
    if by_day:
        day = min(by_day)
        return min(by_day[day]).isoformat() if by_day[day] else day


def observe(previous, rows, stamp):
    """Keep compact evidence when a posting leaves the feed and later returns."""
    history = dict(previous.get("observations", {}))
    for old in previous.get("candidates", []):
        if old["id"] not in history:
            history[old["id"]] = {
                "first_seen_at": old.get("first_seen_at") or old.get("fetched_at"),
                "published_at": old.get("published_at") or old.get("posted_date"),
                "fingerprint": _fingerprint(old), "requisition": old.get("requisition"),
            }
    current_ids = {row["id"] for row in rows}
    gone_content = {}
    for key, obs in history.items():
        if key not in current_ids and obs.get("fingerprint"):
            gone_content.setdefault(obs["fingerprint"], set()).add(obs.get("requisition"))
    requisitions = {}
    for obs in history.values():
        key = obs.get("requisition")
        if key:
            requisitions.setdefault(key, []).append(obs)
    for row in rows:
        old = history.get(row["id"], {})
        related = [old, *requisitions.get(row.get("requisition"), [])]
        first = min(filter(None, [instant(stamp), *(instant(r.get("first_seen_at")) for r in related)]))
        published = _original_publication([row, *related], first)
        signature = _fingerprint(row)
        earlier_ids = gone_content.get(signature, set())
        possible = any(prior.get("possible_repost") for prior in related)
        if not any(related) and earlier_ids:
            # Different known requisitions are independent openings even if
            # the employer reused the entire description.
            possible = not row.get("requisition") or None in earlier_ids
        row.update(first_seen_at=first.isoformat(), last_verified_at=stamp, published_at=published,
                   posted_date=published[:10] if published else None, possible_repost=possible)
        history[row["id"]] = {"first_seen_at": row["first_seen_at"], "published_at": published,
                              "requisition": row.get("requisition"), "fingerprint": signature,
                              "possible_repost": possible}
    return history
