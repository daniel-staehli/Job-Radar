from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

from .codex_client import codex_status
from .config import ROOT, api_key_available
from .db import JobDatabase
from .manual_import import add_job_to_selection, import_manual_data, import_manual_job
from .report import write_report
from .dashboard import build_dashboard, format_dashboard, write_dashboard
from .tracking import STATUSES, ensure_tracking_schema, get_application, list_events, update_application


def iso_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD for dates")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a valid YYYY-MM-DD date") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="job-radar",
        description=(
            "Import jobs you choose, track applications and review follow-ups locally. "
            "AI search and document generation remain optional experiments."
        ),
    )
    root.add_argument(
        "--db", type=Path, default=ROOT / "job_radar.sqlite3", help="SQLite database"
    )
    root.set_defaults(handler=handle_dashboard, as_of=None, stale_days=21, output_dir=None)
    commands = root.add_subparsers(dest="command")

    dashboard = commands.add_parser("dashboard", help="Show application progress and follow-ups (default; offline)")
    dashboard.add_argument("--as-of", type=iso_date)
    dashboard.add_argument("--stale-days", type=int, default=21)
    dashboard.add_argument("--output-dir", type=Path, help="Also export Markdown, CSV and JSON")
    dashboard.set_defaults(handler=handle_dashboard)

    jobs = commands.add_parser("jobs", help="List saved job IDs, including closed vacancies")
    jobs.add_argument("--untracked", action="store_true")
    jobs.set_defaults(handler=handle_jobs)

    add = commands.add_parser("add-job", help="Save a vacancy and start tracking it as interested (offline)")
    for name in ("company", "title", "location", "url"):
        add.add_argument("--" + name, required=True)
    description = add.add_mutually_exclusive_group(required=True)
    description.add_argument("--description")
    description.add_argument("--description-file", type=Path, help="UTF-8 text copied from the vacancy")
    add.add_argument("--role-family")
    add.add_argument("--source", help="Where you found it, e.g. LinkedIn or referral")
    add.add_argument("--on", type=iso_date, help="Initial tracking date for a historical import")
    add.add_argument("--dry-run", action="store_true")
    add.set_defaults(handler=handle_add_job)

    track = commands.add_parser("track", help="Record application progress, documents and next actions")
    track.add_argument("job_id")
    track.add_argument("--status", choices=STATUSES)
    for name in ("application-date", "cv-version", "cover-letter", "contact", "next-action",
                 "follow-up-date", "interview-stage", "outcome", "notes", "source"):
        track.add_argument("--" + name)
    track.add_argument("--interview-date", action="append", help="Add an interview date; repeat for multiple dates")
    track.add_argument("--on", type=iso_date, help="Effective event date; enter historical events in order")
    track.add_argument("--clear", action="append", default=[], choices=(
        "application-date", "cv-version", "cover-letter", "contact", "next-action", "follow-up-date",
        "interview-stage", "interview-dates", "outcome", "notes"), help="Clear a field explicitly; repeat as needed")
    track.add_argument("--dry-run", action="store_true")
    track.set_defaults(handler=handle_track)

    history = commands.add_parser("history", help="Read an application's complete dated audit history")
    history.add_argument("job_id")
    history.set_defaults(handler=handle_history)

    init = commands.add_parser("init", help="Initialize the SQLite database")
    init.set_defaults(handler=handle_init)

    search = commands.add_parser("search", help="Experimental: AI web search (paid API unless --dry-run)")
    search.add_argument("--mode", choices=("initial", "update"), default="initial")
    search.add_argument("--passes", type=int, default=20)
    search.add_argument("--since-days", type=int, default=7)
    search.add_argument("--max-results-per-pass", type=int, default=12)
    search.add_argument("--dry-run", action="store_true")
    search.add_argument(
        "--profile", type=Path, default=ROOT / "data" / "master_profile.yaml"
    )
    search.add_argument(
        "--strategies", type=Path, default=ROOT / "data" / "search_strategies.yaml"
    )
    search.add_argument(
        "--prompt-dir", type=Path, default=ROOT / "reports" / "search-prompts"
    )
    search.set_defaults(handler=handle_search)

    verify = commands.add_parser("verify", help="Recheck official job URLs")
    verify.add_argument("--limit", type=int, default=200)
    verify.set_defaults(handler=handle_verify)

    report = commands.add_parser("report", help="Export the existing vacancy shortlist; use dashboard for applications")
    report.add_argument("--top", type=int, default=50)
    report.add_argument("--changed-only", action="store_true")
    report.add_argument("--mark-reported", action="store_true")
    report.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    report.set_defaults(handler=handle_report)

    manual = commands.add_parser(
        "import-job",
        help="Import one manually supplied YAML vacancy without an API search",
    )
    manual.add_argument(
        "--file", type=Path, required=True, help="YAML vacancy; see examples/manual_job.yaml"
    )
    manual.add_argument(
        "--select",
        action="store_true",
        help="Also add the imported vacancy to selected_jobs.yaml",
    )
    manual.add_argument(
        "--selection", type=Path, default=ROOT / "data" / "selected_jobs.yaml"
    )
    manual.add_argument("--source", help="Override the source label when starting a new application record")
    manual.add_argument("--on", type=iso_date, help="Initial tracking date for a historical import")
    manual.add_argument("--dry-run", action="store_true", help="Validate without writing any files")
    manual.set_defaults(handler=handle_import_job)

    url_import = commands.add_parser("import-url", help="Turn a vacancy link into a reviewable YAML file (no AI)")
    url_import.add_argument("url")
    url_import.add_argument("--output", type=Path, help="Explicit YAML filename")
    url_import.add_argument("--output-dir", type=Path, default=ROOT / "data" / "imported_jobs")
    url_import.add_argument("--overwrite", action="store_true")
    url_import.add_argument("--save", action="store_true", help="Also save a complete vacancy in the database")
    url_import.add_argument("--select", action="store_true", help="Also save and add to the current application selection")
    url_import.add_argument("--selection", type=Path, default=ROOT / "data" / "current_application.yaml")
    for name in ("company", "title", "location", "source", "role-family"):
        url_import.add_argument("--" + name, help="Supply or correct this field")
    url_import.add_argument("--description-file", type=Path, help="UTF-8 vacancy text if extraction is blocked")
    url_import.add_argument("--dry-run", action="store_true", help="Fetch and preview; write nothing, no AI")
    url_import.set_defaults(handler=handle_import_url)

    references = commands.add_parser("references", help="Manage a private, local library of example CVs and cover letters")
    reference_commands = references.add_subparsers(dest="reference_command", required=True)
    for name in ("add", "list", "show", "remove", "set-type"):
        command = reference_commands.add_parser(name)
        command.add_argument("--library-dir", type=Path, default=ROOT / "data" / "reference_cvs")
        command.set_defaults(handler=handle_references)
        if name == "add":
            command.add_argument("file", type=Path)
            command.add_argument("--type", dest="document_type", choices=("cv", "cover_letter"), default="cv")
            command.add_argument("--label")
            command.add_argument("--tag", action="append", default=[])
            command.add_argument("--notes", action="append", help="A note about this reference; repeat as needed")
        elif name in ("show", "remove", "set-type"):
            command.add_argument("reference_id")
            if name == "set-type":
                command.add_argument("--type", dest="document_type", choices=("cv", "cover_letter"), required=True)

    applications = commands.add_parser(
        "generate-applications",
        help="Optional: generate reviewed drafts using Codex or paid API",
    )
    _add_application_arguments(applications)
    applications.set_defaults(handler=handle_applications)

    cvs = commands.add_parser(
        "generate-cvs",
        help="Compatibility alias for generate-applications",
    )
    _add_application_arguments(cvs)
    cvs.set_defaults(handler=handle_applications)

    status = commands.add_parser("status", help="Show local readiness")
    status.set_defaults(handler=handle_status)
    return root


def _add_application_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--job", action="append", help="Generate only this saved job ID; repeat as needed; ignores --selection entirely, including its references")
    command.add_argument("--reference", action="append", help="Reference CV ID for style and emphasis; repeat up to three")
    command.add_argument("--reference-library", type=Path, default=ROOT / "data" / "reference_cvs")
    command.add_argument("--offline", action="store_true", help="Use saved vacancy text instead of fetching the page; actual generation still calls the model")
    command.add_argument(
        "--selection", type=Path, default=ROOT / "data" / "selected_jobs.yaml"
    )
    command.add_argument(
        "--profile", type=Path, default=ROOT / "data" / "master_profile.yaml"
    )
    command.add_argument(
        "--template", type=Path, default=ROOT / "templates" / "cv_default.tex.j2"
    )
    command.add_argument(
        "--letter-template",
        type=Path,
        default=ROOT / "templates" / "motivation_letter.tex.j2",
    )
    command.add_argument(
        "--backend",
        choices=("codex", "api"),
        default="codex",
        help="Codex reuses ChatGPT login; API uses OPENAI_API_KEY",
    )
    command.add_argument("--output-root", type=Path, default=ROOT / "applications")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--no-compile", action="store_true")
    command.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing validated Codex output instead of requesting it again",
    )


def database(args: argparse.Namespace) -> JobDatabase:
    db = JobDatabase(args.db)
    db.initialize()
    return db


def handle_init(args: argparse.Namespace) -> int:
    backup = ensure_tracking_schema(JobDatabase(args.db))
    print(f"Initialized {args.db}")
    if backup:
        print(f"Pre-migration backup: {backup}")
    return 0


def handle_dashboard(args: argparse.Namespace) -> int:
    report = build_dashboard(JobDatabase(args.db), as_of=args.as_of, stale_days=args.stale_days)
    print(format_dashboard(report))
    if args.output_dir:
        for path in write_dashboard(report, args.output_dir):
            print(path)
    if not report["summary"]["tracked_applications"]:
        print("Start with: import-job --file examples/manual_job.yaml (edit a copy first).")
        print("Use jobs --untracked to find existing vacancy IDs, then track JOB_ID --status interested.")
    return 0


def handle_jobs(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    if not db.path.exists() or not db.path.stat().st_size:
        print("No saved vacancies. Use add-job or import-job to save one.")
        return 0
    with sqlite3.connect(db.path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
            print("No saved vacancies.")
            return 0
        rows = connection.execute("SELECT * FROM jobs ORDER BY company COLLATE NOCASE, title").fetchall()
    count = 0
    for row in rows:
        application = get_application(db, row["job_id"])
        if args.untracked and application:
            continue
        status = application["status"] if application else "untracked"
        closed = " [vacancy closed]" if row["verification_status"] == "closed" else ""
        print(f"{row['job_id']} | {status} | {row['company']} - {row['title']} ({row['location']}){closed}")
        count += 1
    print(f"{count} vacancies")
    return 0


def handle_add_job(args: argparse.Namespace) -> int:
    description = args.description_file.read_text(encoding="utf-8-sig") if args.description_file else args.description
    raw = {"company": args.company, "title": args.title, "location": args.location,
           "source_url": args.url, "description": description}
    if args.role_family is not None:
        raw["role_family"] = args.role_family
    job_id, action = import_manual_data(JobDatabase(args.db), raw, source=args.source,
                                       dry_run=args.dry_run, on_date=args.on)
    print(f"{'Preview (no changes): ' if args.dry_run else ''}{action}: {job_id}")
    return 0


def handle_track(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    fields = ("status", "application_date", "cv_version", "cover_letter", "contact", "next_action",
              "follow_up_date", "interview_stage", "outcome", "notes", "source")
    changes = {field: getattr(args, field) for field in fields if getattr(args, field) is not None}
    for field in args.clear:
        name = field.replace("-", "_")
        if name in changes or (name == "interview_dates" and args.interview_date):
            raise ValueError(f"Cannot set and clear {field} in the same command")
        changes[name] = [] if name == "interview_dates" else None
    previous = get_application(db, args.job_id)
    if args.interview_date:
        dates = previous["interview_dates"] if previous else []
        changes["interview_dates"] = sorted(set(dates + args.interview_date))
    if not changes:
        if previous is None:
            raise ValueError("Start tracking with --status; use jobs to find valid vacancy IDs")
        print(json.dumps(previous, indent=2, ensure_ascii=False))
        return 0
    result = update_application(db, args.job_id, changes, on_date=args.on, dry_run=args.dry_run)
    if args.dry_run:
        print("Preview only; no database, backup or history changes.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_history(args: argparse.Namespace) -> int:
    events = list_events(JobDatabase(args.db), args.job_id)
    if not events:
        print("No application history recorded for this job ID. Use jobs to list saved vacancies.")
    else:
        print(json.dumps(events, indent=2, ensure_ascii=False))
    return 0


def handle_search(args: argparse.Namespace) -> int:
    from .search import run_search

    totals = run_search(
        db=database(args),
        profile_path=args.profile,
        strategies_path=args.strategies,
        mode=args.mode,
        passes=args.passes,
        since_days=args.since_days,
        max_results=args.max_results_per_pass,
        dry_run=args.dry_run,
        prompt_dir=args.prompt_dir,
    )
    print(json.dumps(totals, indent=2))
    if args.dry_run:
        print(f"Prompts written to {args.prompt_dir}")
    return 0


def handle_verify(args: argparse.Namespace) -> int:
    from .search import verify_jobs

    result = verify_jobs(database(args), limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def handle_report(args: argparse.Namespace) -> int:
    paths = write_report(
        db=database(args),
        output_dir=args.output_dir,
        top=args.top,
        changed_only=args.changed_only,
        mark_reported=args.mark_reported,
    )
    for path in paths[:3]:
        print(path)
    print(f"{len(paths[3])} jobs included")
    return 0


def handle_import_job(args: argparse.Namespace) -> int:
    job_id, action = import_manual_job(JobDatabase(args.db), args.file, dry_run=args.dry_run,
                                      source=args.source, on_date=args.on)
    print(f"{'Preview (no changes): ' if args.dry_run else ''}{action}: {job_id}")
    if args.select:
        if args.dry_run:
            print(f"Would select in {args.selection}")
        else:
            add_job_to_selection(args.selection, job_id)
            print(f"Selected in {args.selection}")
    return 0


def handle_import_url(args: argparse.Namespace) -> int:
    from .url_import import prepare_job_url

    overrides = {name: getattr(args, name) for name in ("company", "title", "location", "source", "role_family")
                 if getattr(args, name) is not None}
    if args.description_file:
        overrides["description"] = args.description_file.read_text(encoding="utf-8-sig")
    result = prepare_job_url(args.url, output_dir=args.output_dir, output_path=args.output,
                             overwrite=args.overwrite, dry_run=args.dry_run, overrides=overrides)
    if args.dry_run:
        import yaml
        print(yaml.safe_dump(result["job"], allow_unicode=True, sort_keys=False))
        print("Preview only: no files or database changes. No model was called.")
    else:
        print(f"Vacancy YAML: {result['yaml_path']}")
        print(f"Extraction notes: {result['audit_path']}")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    if not result["complete"]:
        print("Draft needs review; missing: " + ", ".join(result["missing_fields"]))
        print("Fill these fields in the YAML, then use import-job --file with that file.")
        return 1 if args.save or args.select else 0
    if args.save or args.select:
        job_id, action = import_manual_data(JobDatabase(args.db), result["job"], dry_run=args.dry_run)
        print(f"{'Would save: ' if args.dry_run else ''}{action}: {job_id}")
        if args.select:
            if not args.dry_run:
                add_job_to_selection(args.selection, job_id)
            print(f"{'Would select' if args.dry_run else 'Selected'} in {args.selection}")
        print(f"Next: generate-applications --job {job_id} --offline --dry-run")
    else:
        print("Review the YAML, then use import-job --file with that file to save it.")
    return 0


def handle_references(args: argparse.Namespace) -> int:
    from .references import add_reference, list_references, load_references, remove_reference, set_reference_type

    if args.reference_command == "add":
        item = add_reference(args.library_dir, args.file, label=args.label, tags=args.tag, notes=args.notes,
                             document_type=args.document_type)
        print(f"Reference saved locally: {item['id']} | {item['label']}")
        print(f"Use for generation: --reference {item['id']}")
    elif args.reference_command == "list":
        items = list_references(args.library_dir)
        for item in items:
            print(f"{item['id']} | {item['document_type']} | {item['label']} | {', '.join(item['tags'])} | {item['source_name']}")
        print(f"{len(items)} reference documents. Adding a reference does not send it to a model.")
    elif args.reference_command == "set-type":
        item = set_reference_type(args.library_dir, args.reference_id, args.document_type)
        print(f"Reference type updated: {item['id']} | {item['document_type']}")
    elif args.reference_command == "show":
        item = load_references(args.library_dir, [args.reference_id])[0]
        print(json.dumps(item, indent=2, ensure_ascii=False))
    else:
        item = remove_reference(args.library_dir, args.reference_id)
        print(f"Removed library copy: {item['id']}. The original source file is unchanged.")
    return 0


def handle_applications(args: argparse.Namespace) -> int:
    from .cv import generate_cvs

    outputs = generate_cvs(
        db=JobDatabase(args.db),
        selection_path=args.selection,
        profile_path=args.profile,
        template_path=args.template,
        letter_template_path=args.letter_template,
        output_root=args.output_root,
        dry_run=args.dry_run,
        compile_pdf=not args.no_compile,
        backend=args.backend,
        resume=args.resume,
        job_ids_override=args.job,
        reference_ids=args.reference,
        reference_library=args.reference_library,
        offline=args.offline,
    )
    for output in outputs:
        print(output)
    return 0


def handle_status(args: argparse.Namespace) -> int:
    print(f"Project root: {ROOT}")
    print(f"Database: {args.db}")
    print(f"OPENAI_API_KEY: {'available' if api_key_available() else 'not set'}")
    try:
        status = codex_status()
        print(
            "Codex CLI: "
            + (
                str(status.get("version") or "installed")
                if status["installed"]
                else "not installed"
            )
        )
        print(
            "Codex ChatGPT login: "
            + ("ready" if status["authenticated"] else "not ready")
        )
        if status.get("detail"):
            print(f"Codex detail: {status['detail']}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Codex CLI: status check failed ({exc.__class__.__name__})")
    print("LaTeX compilation: automatic when latexmk is installed")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
