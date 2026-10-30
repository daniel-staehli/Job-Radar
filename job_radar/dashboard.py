"""Offline, read-only application reporting from explicit tracking history."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from uuid import uuid4

from .config import local_today
from .db import JobDatabase
from .tracking import STATUSES, list_applications, list_events


_INACTIVE = {"rejected", "withdrawn"}
_METADATA = ("job_id", "company", "title", "role_family", "location",
             "source_url", "verification_status")


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _bucket(value: object) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "unknown"


def _vacancy_count(db: JobDatabase) -> int:
    """Avoid JobDatabase.connect(), which is a write-capable connection."""
    path = Path(db.path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]) if exists else 0


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _metrics(rows: list[dict]) -> dict:
    submitted = [row for row in rows if row["is_submitted"]]
    interviews = sum(row["reached_interview"] for row in submitted)
    offers = sum(row["reached_offer"] for row in submitted)
    return {
        "tracked": len(rows),
        "submitted": len(submitted),
        "interviews": interviews,
        "offers": offers,
        "interview_rate": _rate(interviews, len(submitted)),
        "offer_rate": _rate(offers, len(submitted)),
    }


def build_dashboard(
    db: JobDatabase, as_of: date | None = None, stale_days: int = 21
) -> dict:
    """Return JSON-safe current/historical state; never migrate or write the DB.

    Rates are fractions, or None for an empty denominator. Historical state is
    reconstructed by effective event date, not audit-recording timestamp.
    """
    if stale_days < 1:
        raise ValueError("stale_days must be at least 1")
    day = as_of or local_today()
    if not isinstance(day, date) or isinstance(day, datetime):
        raise TypeError("as_of must be a date")
    applications = [dict(row) for row in list_applications(db)]
    by_job: dict[str, list[dict]] = defaultdict(list)
    for event in list_events(db):
        by_job[str(event["job_id"])].append(dict(event))

    rows: list[dict] = []
    not_yet_effective = 0
    no_history = 0
    for current in applications:
        events = by_job.get(str(current["job_id"]), [])
        eligible = [event for event in events
                    if (effective := _date(event.get("occurred_at"))) is not None
                    and effective <= day]
        eligible.sort(key=lambda event: (_date(event["occurred_at"]), event.get("id", 0)))
        if eligible:
            state = {key: current.get(key) for key in _METADATA}
            state.update(eligible[-1].get("snapshot") or {})
            state["status"] = eligible[-1].get("new_status") or state.get("status")
        elif events or ((_date(current.get("created_at")) or day) > day):
            not_yet_effective += 1
            continue
        else:
            # Support existing explicit tracking rows without invented history.
            state = dict(current)
            no_history += 1

        state["job_id"] = str(current["job_id"])
        submitted_on = _date(state.get("application_date"))
        is_submitted = submitted_on is not None and submitted_on <= day
        reached = {event.get("new_status") for event in eligible}
        if not events:
            reached.add(state.get("status"))
        original_applied = next(
            (event for event in eligible if event.get("new_status") == "applied"), None
        )
        attribution = (original_applied.get("snapshot") or {}) if original_applied else state
        # Filling a previously unknown submitted-document/source label is useful
        # for backfilled records; never replace an originally known label silently.
        source = _bucket(attribution.get("source"))
        cv_version = _bucket(attribution.get("cv_version"))
        completed_metadata = False
        if source == "unknown":
            source = _bucket(state.get("source"))
            completed_metadata |= source != "unknown"
        if cv_version == "unknown":
            cv_version = _bucket(state.get("cv_version"))
            completed_metadata |= cv_version != "unknown"
        dates = [_date(event.get("occurred_at")) for event in eligible]
        if is_submitted:
            dates.append(submitted_on)
        last_activity = max((value for value in dates if value is not None), default=None)
        active = state.get("status") not in _INACTIVE
        follow_up = _date(state.get("follow_up_date"))
        interview_dates = sorted({value.isoformat()
                                  for item in state.get("interview_dates", [])
                                  if (value := _date(item)) is not None and value >= day})
        state.update({
            "is_active": active,
            "is_submitted": is_submitted,
            "reached_interview": "interview" in reached,
            "reached_offer": "offer" in reached,
            "last_activity_date": last_activity.isoformat() if last_activity else None,
            "days_since_activity": (day - last_activity).days if last_activity else None,
            "follow_up_due": active and follow_up is not None and follow_up <= day,
            "is_stale": bool(is_submitted and state.get("status") in {"applied", "interview"}
                             and last_activity and (day - last_activity).days >= stale_days),
            "upcoming_interview_dates": interview_dates if active else [],
            "metric_source": source,
            "metric_cv_version": cv_version,
            "metric_attribution": ("original_applied_event_with_completed_metadata" if completed_metadata
                                   else "original_applied_event") if original_applied else "effective_record",
        })
        rows.append(state)

    rows.sort(key=lambda row: (str(row.get("company") or "").casefold(),
                               str(row.get("title") or "").casefold(), row["job_id"]))
    active = [row for row in rows if row["is_active"]]
    due = sorted((row for row in rows if row["follow_up_due"]),
                 key=lambda row: (row["follow_up_date"], row["job_id"]))
    stale = sorted((row for row in rows if row["is_stale"]),
                   key=lambda row: (-row["days_since_activity"], row["job_id"]))
    upcoming = [
        {**{key: row.get(key) for key in (*_METADATA, "status", "interview_stage", "contact")},
         "interview_date": when}
        for row in rows for when in row["upcoming_interview_dates"]
    ]
    upcoming.sort(key=lambda row: (row["interview_date"], row["job_id"]))
    groups = {}
    for dimension, key in (("role_family", "role_family"), ("source", "metric_source"),
                           ("cv_version", "metric_cv_version")):
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[_bucket(row.get(key))].append(row)
        groups[dimension] = [{"group": label, **_metrics(buckets[label])}
                             for label in sorted(buckets, key=str.casefold)]

    total = _vacancy_count(db)
    counts = Counter(row.get("status") for row in rows)
    metrics = _metrics(rows)
    return {
        "as_of": day.isoformat(),
        "stale_days": stale_days,
        "summary": {
            "total_vacancies": total,
            "tracked_applications": len(rows),
            "untracked_vacancies": max(0, total - len(applications)),
            "tracking_records_not_yet_effective": not_yet_effective,
            "active_applications": len(active),
            "due_followups": len(due),
            "stale_applications": len(stale),
            "upcoming_interviews": len(upcoming),
            "submitted_applications": metrics["submitted"],
            "applications_reaching_interview": metrics["interviews"],
            "applications_reaching_offer": metrics["offers"],
            "interview_rate": metrics["interview_rate"],
            "offer_rate": metrics["offer_rate"],
            "tracking_records_without_event_history": no_history,
        },
        "status_counts": {status: counts[status] for status in STATUSES},
        "applications": rows,
        "active_applications": active,
        "due_followups": due,
        "stale_applications": stale,
        "upcoming_interviews": upcoming,
        "groups": groups,
        "methodology": [
            "Only explicitly tracked applications are analysed; legacy vacancies are not inferred applications. Vacancy inventory counts describe the database now.",
            "Application state uses event snapshots effective on or before the as-of date. Later-recorded historical events are included by their effective date.",
            "Active means any status except rejected or withdrawn; offers remain visible for next actions. Vacancy-page closure does not exclude a tracked application.",
            "Each rate denominator is applications with an explicit submission date on or before the as-of date. The numerator is distinct submitted applications that reached that status by that date, including those later rejected or withdrawn.",
            "Interview status is an explicitly recorded interview stage, not proof of attendance. Scheduled interview dates alone never count toward interview conversion; offers do not imply an unrecorded interview.",
            "Source and CV attribution retain known values from the earliest applied-event snapshot. Previously missing values may be completed from the effective record; remaining gaps use an unknown bucket. Role family uses current vacancy metadata.",
            "Staleness applies only to applied/interview records with a submission date and uses the latest effective event or submission date, excluding future dates.",
            "Rates are descriptive, can have very small samples, and do not show that a source or CV version caused an outcome. A missing denominator is shown as n/a.",
            "Rows without event history use their explicit stored state; historical status changes cannot be reconstructed for those rows.",
        ],
    }


def _cell(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=False).replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _table(columns: list[tuple[str, str]], rows: list[dict]) -> list[str]:
    if not rows:
        return ["None.", ""]
    return ["| " + " | ".join(label for _, label in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
            *("| " + " | ".join(_cell(row.get(key)) for key, _ in columns) + " |"
              for row in rows), ""]


def format_dashboard(report: dict) -> str:
    """Format a deterministic Markdown report suitable for a terminal/file."""
    summary = report["summary"]
    lines = [f"# Application dashboard - {report['as_of']}", "",
             f"Tracked: {summary['tracked_applications']} | Active: {summary['active_applications']} | "
             f"Due follow-ups: {summary['due_followups']} | Stale: {summary['stale_applications']}",
             f"Untracked vacancies: {summary['untracked_vacancies']} | "
             f"Tracking records not yet effective: {summary['tracking_records_not_yet_effective']}", "",
             f"Submitted: {summary['submitted_applications']} | "
             f"Reached interview: {summary['applications_reaching_interview']} ({_percent(summary['interview_rate'])}) | "
             f"Reached offer: {summary['applications_reaching_offer']} ({_percent(summary['offer_rate'])})", "",
             "## Status counts", ""]
    lines += _table([("status", "Status"), ("count", "Count")],
                    [{"status": key, "count": value} for key, value in report["status_counts"].items()])
    base = [("job_id", "Job ID"), ("company", "Company"), ("title", "Role"), ("status", "Status")]
    for heading, key, extra in [
        ("Due follow-ups", "due_followups", [("follow_up_date", "Due"), ("next_action", "Next action")]),
        (f"Stale submitted applications ({report['stale_days']}+ days)", "stale_applications",
         [("last_activity_date", "Last activity"), ("days_since_activity", "Days"), ("next_action", "Next action")]),
        ("Upcoming scheduled interviews", "upcoming_interviews", [("interview_date", "Date"), ("interview_stage", "Stage")]),
        ("Active applications", "active_applications", [("application_date", "Submitted"), ("next_action", "Next action")]),
    ]:
        lines += [f"## {heading}", ""] + _table(base + extra, report[key])
    for dimension, groups in report["groups"].items():
        formatted = [{**row, "interview_rate": _percent(row["interview_rate"]),
                      "offer_rate": _percent(row["offer_rate"])} for row in groups]
        lines += [f"## Performance by {dimension.replace('_', ' ')}", ""]
        lines += _table([("group", "Group"), ("tracked", "Tracked"), ("submitted", "Submitted"),
                         ("interviews", "Interviews"), ("interview_rate", "Interview rate"),
                         ("offers", "Offers"), ("offer_rate", "Offer rate")], formatted)
    lines += ["## Reading these numbers", ""]
    lines += [f"- {item}" for item in report["methodology"]]
    return "\n".join(lines) + "\n"


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    if text and (text[0] in "\t\r\n" or text.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + text
    return text


def write_dashboard(report: dict, output_dir: Path) -> tuple[Path, Path, Path]:
    """Export Markdown/CSV/JSON under a unique stem; never overwrite a report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    stem = f"application-dashboard-{report['as_of']}-{stamp}-{uuid4().hex[:12]}"
    markdown_path, csv_path, json_path = [output_dir / f"{stem}.{ext}" for ext in ("md", "csv", "json")]
    with markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(format_dashboard(report))
    fields = ["job_id", "company", "title", "location", "role_family", "source_url",
              "verification_status", "status", "application_date", "cv_version", "cover_letter",
              "source", "contact", "next_action", "follow_up_date", "interview_stage",
              "interview_dates", "outcome", "notes", "is_active", "is_submitted",
              "reached_interview", "reached_offer", "last_activity_date", "days_since_activity",
              "follow_up_due", "is_stale", "metric_source", "metric_cv_version", "metric_attribution"]
    with csv_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["applications"]:
            writer.writerow({key: _csv_safe("; ".join(row.get(key) or []) if key == "interview_dates"
                                            else row.get(key)) for key in fields})
    with json_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return markdown_path, csv_path, json_path
