from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import local_today
from .db import JobDatabase


def _cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def write_report(
    *,
    db: JobDatabase,
    output_dir: Path,
    top: int,
    changed_only: bool,
    mark_reported: bool,
) -> tuple[Path, Path, Path, list]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = db.list_jobs(limit=top, changed_only=changed_only)
    stamp = local_today().isoformat()
    stem = f"job-report-{stamp}{'-changes' if changed_only else ''}"
    markdown_path = output_dir / f"{stem}.md"
    csv_path = output_dir / f"{stem}.csv"
    json_path = output_dir / f"{stem}.json"

    lines = [
        f"# Job radar — {stamp}",
        "",
        "Ranking weights: 40% hiring realism, 35% functional fit, "
        "25% long-term goal value.",
        "",
        "| # | ID | Role | Company | Location | Mode | Realism | Fit | Goal | Priority | Status |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {index} | `{job_id}` | [{title}]({url}) | {company} | "
            "{location} | {mode} | {realism} | {fit} | {goal} | "
            "{priority:.1f} | {status} |".format(
                index=index,
                job_id=row["job_id"],
                title=_cell(row["title"]),
                url=row["source_url"],
                company=_cell(row["company"]),
                location=_cell(row["location"]),
                mode=row["work_mode"],
                realism=row["hiring_realism"],
                fit=row["functional_fit"],
                goal=row["goal_value"],
                priority=row["overall_priority"],
                status=row["verification_status"],
            )
        )

    for row in rows:
        payload = json.loads(row["payload_json"])
        lines.extend(
            [
                "",
                f"## {row['title']} — {row['company']}",
                "",
                f"- ID: `{row['job_id']}`",
                f"- Link: {row['source_url']}",
                f"- Why it could fit: {'; '.join(payload['why_fit']) or 'Not stated'}",
                f"- Hard gaps: {'; '.join(payload['hard_gaps']) or 'None identified'}",
                f"- Evidence: {'; '.join(payload['evidence']) or 'Not stated'}",
                f"- Salary: {row['salary_text'] or 'Not disclosed'}",
                f"- Verification: {row['verification_status']} — "
                f"{row['verification_note'] or 'not checked'}",
            ]
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fieldnames = [
        "job_id",
        "company",
        "title",
        "location",
        "country",
        "work_mode",
        "posted_date",
        "source_url",
        "salary_text",
        "role_family",
        "functional_fit",
        "hiring_realism",
        "goal_value",
        "overall_priority",
        "verification_status",
        "first_seen_at",
        "last_seen_at",
        "repost_count",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fieldnames})

    json_path.write_text(
        json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if mark_reported:
        db.mark_reported([row["job_id"] for row in rows])
    return markdown_path, csv_path, json_path, rows
