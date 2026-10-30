"""Small, UI-independent adapters for the local Job Radar window.

Opening the window and reading its lists never creates a database or makes a
network request. Vacancy fetching and AI generation are separate explicit
actions; previews always use the saved vacancy text and stay local.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .config import ROOT, load_yaml
from .dashboard import build_dashboard, format_dashboard
from .db import JobDatabase
from .draft_history import publish_revision, resolve_previous_source, source_fingerprint
from .manual_import import import_manual_data
from .models import SearchJob, TailoredApplication
from .generation_state import is_incomplete
from .normalize import stable_job_id
from .references import add_reference, list_references, remove_reference
from .tracking import get_application, list_events, update_application
from .url_import import prepare_job_url


def _readable_fields(value: object, indent: int = 0) -> str:
    """Format saved data for reading without interpreting any of its content."""
    prefix = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = str(key).replace("_", " ").capitalize()
            if isinstance(item, (dict, list)) and item:
                lines.extend((f"{prefix}{label}:", _readable_fields(item, indent + 1)))
            else:
                lines.append(f"{prefix}{label}: {_readable_fields(item).strip()}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "None"
        return "\n".join(
            f"{prefix}- {_readable_fields(item, indent + 1).lstrip()}" for item in value
        )
    if value is None or value == "":
        return "Not supplied"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _review_sections(user: str, *, include_supporting: bool = False) -> tuple:
    """Read the known prompt layout, rejecting partial or ambiguous decoding."""
    decoder = json.JSONDecoder()
    if not user.startswith("JOB\n"):
        raise ValueError("Unknown saved request format")
    job, end = decoder.raw_decode(user[4:])
    remaining = user[4 + end:]
    vacancy_marker = "\n\nFULL JOB-PAGE TEXT\n"
    if not remaining.startswith(vacancy_marker):
        raise ValueError("Unknown saved vacancy format")
    # The verified facts follow the free-text vacancy. Their JSON strings escape
    # line breaks, so the last complete marker is the actual section boundary.
    description, boundary, remaining = remaining[len(vacancy_marker):].rpartition(
        "\n\nVERIFIED CANDIDATE FACTS\n"
    )
    if not boundary:
        raise ValueError("Missing saved profile")
    profile, end = decoder.raw_decode(remaining)
    remaining = remaining[end:]
    if remaining.startswith("\n\nTASK\n"):
        # Before the reference library existed, the verified-profile JSON was
        # followed directly by TASK. Accept that exact boundary only.
        references = []
    else:
        reference_markers = (
            "\n\nREFERENCE DOCUMENT EXAMPLES (STYLE ONLY, NOT FACTUAL EVIDENCE)\n",
            "\n\nREFERENCE CV EXAMPLES (STYLE ONLY, NOT FACTUAL EVIDENCE)\n",
        )
        reference_marker = next((marker for marker in reference_markers
                                 if remaining.startswith(marker)), None)
        if reference_marker is None:
            raise ValueError("Unknown saved reference format")
        remaining = remaining[len(reference_marker):]
        references, end = decoder.raw_decode(remaining)
        remaining = remaining[end:]
    supporting = {}
    supporting_marker = "\n\nJOB-SPECIFIC SUPPORTING SOURCES (EXTERNAL CONTEXT, NOT CANDIDATE FACTS)\n"
    if remaining.startswith(supporting_marker):
        supporting, end = decoder.raw_decode(remaining[len(supporting_marker):])
        if not isinstance(supporting, dict):
            raise ValueError("Unexpected saved supporting sources")
        remaining = remaining[len(supporting_marker) + end:]
    if not remaining.startswith("\n\nTASK\n"):
        raise ValueError("Missing saved writing task")
    if not isinstance(job, dict) or not isinstance(profile, dict) or not isinstance(references, list):
        raise ValueError("Unexpected saved request data")
    parts = (job, description, profile, references, remaining[len("\n\nTASK\n"):])
    return (*parts, supporting) if include_supporting else parts


def _linked(path: Path) -> bool:
    """Reject symbolic links and Windows junctions, including directory links."""
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0) &
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _saved_json(directory: Path, name: str) -> dict:
    path = directory / name
    if _linked(path) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f"The saved {name} file is unavailable or too large.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"The saved {name} file has an invalid format.")
    return data


def _saved_context(directory: Path) -> dict:
    """Use the original snapshot, never today's profile or reference library."""
    if (directory / "generation_context.json").exists():
        context = _saved_json(directory, "generation_context.json")
        if (context.get("schema_version") != 1 or
                not isinstance(context.get("job"), dict) or
                not isinstance(context.get("profile"), dict)):
            raise ValueError("The saved generation context has an invalid format.")
        return context
    prompt = _saved_json(directory, "prompt.json")
    if not isinstance(prompt.get("user"), str):
        raise ValueError("The saved request has an invalid format.")
    job, description, profile, references, _ = _review_sections(prompt["user"])
    return {"job": job, "profile": profile, "full_description": description,
            "references": references, "legacy": True}


def _document_name(document: str) -> str:
    if document not in {"cv", "cover_letter"}:
        raise ValueError("Choose CV or cover letter.")
    return "CV" if document == "cv" else "Cover letter"


def _plain_cv(context: dict) -> str:
    """Show the selected verified content without internal fact IDs."""
    contact = context["contact"]
    lines = [str(contact.get("name", "")), context["headline"]]
    details = [str(contact[key]) for key in ("location", "phone", "email", "linkedin_url")
               if contact.get(key)]
    if details:
        lines.append(" | ".join(details))
    lines.extend(("", "PROFILE", context["profile_text"]))
    for heading, key in (("RESEARCH EXPERIENCE", "experiences"),
                         ("SELECTED PUBLICATIONS", "publications"),
                         ("CORE COMPETENCIES", "skills"),
                         ("LANGUAGES", "languages"),
                         ("EDUCATION", "education"),
                         ("ADDITIONAL EXPERIENCE", "additional_experience"),
                         ("INTERESTS", "interests")):
        values = context[key]
        if not values:
            continue
        lines.extend(("", heading))
        for item in values:
            if not isinstance(item, dict):
                lines.append(str(item))
            elif key in {"experiences", "additional_experience"}:
                lines.append(" | ".join(str(item[field]) for field in ("role", "location", "dates")
                                        if item.get(field)))
                if item.get("subtitle"):
                    lines.append(str(item["subtitle"]))
                lines.extend("• " + str(bullet["text"]) for bullet in item.get("bullets", []))
                if item.get("text"):
                    lines.append(str(item["text"]))
            elif key == "publications":
                lines.append(str(item.get("citation", "")))
                if item.get("url"):
                    lines.append(str(item["url"]))
            elif key == "skills":
                lines.append(f"{item.get('label', '')}: {item.get('text', '')}")
            elif key == "education":
                lines.append(" | ".join(str(item[field]) for field in ("institution", "degree", "dates")
                                        if item.get(field)))
                if item.get("detail"):
                    lines.append(str(item["detail"]))
    return "\n".join(lines).strip()


@dataclass
class GuiService:
    root: Path = ROOT
    db_path: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.db_path = Path(self.db_path) if self.db_path is not None else self.root / "job_radar.sqlite3"

    @property
    def db(self) -> JobDatabase:
        return JobDatabase(self.db_path)

    @property
    def reference_library(self) -> Path:
        return self.root / "data" / "reference_cvs"

    def jobs(self) -> list[dict]:
        """Return all saved vacancies, including closed and untracked ones."""
        if not self.db_path.is_file() or self.db_path.stat().st_size == 0:
            return []
        connection = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            has_jobs = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if not has_jobs:
                return []
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY last_seen_at DESC, company, title, job_id"
            ).fetchall()
        finally:
            connection.close()
        jobs = []
        for row in rows:
            saved = dict(row)
            payload = json.loads(saved.pop("payload_json"))
            current = {**payload, **saved}
            current["description"] = current.get("description_excerpt", "")
            application = get_application(self.db, current["job_id"])
            current.update({
                "status": "untracked", "source": "unknown", "application_date": None,
                "follow_up_date": None, "next_action": None, "contact": None,
                "notes": None, "cv_version": None, "cover_letter": None,
                "interview_stage": None, "interview_dates": [], "outcome": None,
            })
            if application:
                current.update(application)
            jobs.append(current)
        return jobs

    def dashboard_text(self) -> str:
        return format_dashboard(build_dashboard(self.db))

    def import_url(self, url: str) -> dict:
        """Fetch a vacancy and save its reviewable YAML; do not import the draft."""
        return prepare_job_url(url, output_dir=self.root / "data" / "imported_jobs")

    def import_text(self, text: str, *, source_url: str = "") -> dict:
        """Extract a local text draft without a model, website or database write."""
        from .text_import import prepare_job_text
        return prepare_job_text(text, source_url=source_url,
                                output_dir=self.root / "data" / "imported_jobs")

    def import_text_with_ai(self, text: str, *, source_url: str = "",
                            known_fields: dict | None = None,
                            provider_settings: dict | None = None) -> dict:
        """Suggest missing fields on explicit request; save only a reviewable draft."""
        from .ai_text_import import prepare_job_text_ai
        return prepare_job_text_ai(
            text, source_url=source_url, known_fields=known_fields,
            provider_settings=provider_settings if provider_settings is not None else self.ai_settings(),
            output_dir=self.root / "data" / "imported_jobs",
        )

    def load_draft(self, path: Path) -> dict:
        """Read an incomplete or complete job YAML without changing any records."""
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("Choose a job YAML file smaller than 5 MB.")
        raw = load_yaml(path)
        if not isinstance(raw, dict):
            raise ValueError("Job YAML must contain a mapping of field names to values.")
        unknown = set(raw) - set(SearchJob.model_fields) - {"description", "source"}
        if unknown:
            raise ValueError("Unknown job fields: " + ", ".join(sorted(map(str, unknown))))
        return raw

    def save_job(self, raw: dict) -> tuple[str, str]:
        """Save the reviewed vacancy using the existing import and deduplication."""
        return import_manual_data(self.db, raw)

    def employer_presets(self) -> list[dict]:
        from .discovery import list_presets
        return list_presets(self.root)

    def save_employer(self, raw: dict) -> dict:
        from .discovery import save_preset
        return save_preset(self.root, raw)

    def remove_employer(self, preset_id: str) -> None:
        from .discovery import remove_preset
        remove_preset(self.root, preset_id)

    def discovery_candidates(self) -> list[dict]:
        from .discovery import list_candidates
        return list_candidates(self.root)

    def refresh_employers(self, preset_ids: list[str] | None = None) -> dict:
        from .discovery import refresh_presets
        return refresh_presets(self.root, preset_ids)

    def save_discovered_job(self, candidate: dict, edited: dict | None = None) -> tuple[str, str]:
        from .discovery import import_candidate
        # Source identity is deliberately separate from editable vacancy text.
        value = dict(candidate)
        if edited is not None:
            allowed = set(SearchJob.model_fields) | {'description', 'source'}
            value.update({key: item for key, item in edited.items() if key in allowed})
        return import_candidate(self.db, value)

    def update_tracking(self, job_id: str, changes: dict) -> dict:
        return update_application(self.db, job_id, changes)

    def history(self, job_id: str) -> list[dict]:
        return list_events(self.db, job_id)

    def refs(self) -> list[dict]:
        return list_references(self.reference_library)

    def add_ref(self, path: Path, label: str | None = None,
                document_type: str = "cv") -> dict:
        return add_reference(self.reference_library, Path(path), label=label,
                             document_type=document_type)

    def set_ref_type(self, reference_id: str, document_type: str) -> dict:
        from .references import set_reference_type

        return set_reference_type(self.reference_library, reference_id, document_type)

    def remove_ref(self, reference_id: str) -> dict:
        return remove_reference(self.reference_library, reference_id)

    def preview(self, job_id: str, reference_ids: list[str],
                output_root: Path | None = None, provider_settings: dict | None = None) -> dict:
        return self._documents(job_id, reference_ids, output_root, dry_run=True, compile_pdf=False,
                               provider_settings=provider_settings)

    def review_text(self, result: dict) -> str:
        """Show the prepared snapshot, without reopening current private inputs."""
        outputs = result.get("outputs", [])
        if not outputs:
            raise ValueError("No saved information is available to review.")
        directory = Path(outputs[0])
        prompt = json.loads((directory / "prompt.json").read_text(encoding="utf-8"))
        if not isinstance(prompt, dict) or not isinstance(prompt.get("user"), str):
            raise ValueError("The saved information file has an invalid format.")
        user = prompt["user"]
        system = prompt.get("system", "")
        if not isinstance(system, str):
            raise ValueError("The saved writing instructions have an invalid format.")
        saved_description = (directory / "job_description.md").read_text(encoding="utf-8")
        introduction = (
            "INFORMATION FOR YOUR CV AND COVER LETTER\n\n"
            "This is a local review of the information prepared for the AI. "
            "No CV or cover letter has been generated, and nothing has been sent.\n\n"
            "When you choose Generate drafts, the saved vacancy, your verified profile "
            "and the selected reference text below will be sent with writing instructions. "
            "References guide wording and structure; factual claims must come from your "
            "verified profile. No application is submitted.\n\n"
            "This view shows a saved snapshot. If you change your selection or profile, "
            "review the information again before generating.\n\n"
        )
        try:
            job, description, profile, references, task, supporting = _review_sections(user, include_supporting=True)
        except (ValueError, TypeError):
            return (introduction + "SAVED VACANCY\n\n" + saved_description +
                    "\n\nSAVED SOURCE INFORMATION\n\n" + user +
                    "\n\nWRITING INSTRUCTIONS\n\n" + system)
        # The long vacancy text is shown once; all other saved job fields remain
        # visible, including any short excerpt that differs from the full text.
        details = dict(job)
        if details.get("description_excerpt") == description:
            details.pop("description_excerpt")
        selected = _readable_fields(references) if references else "No reference documents selected."
        return (
            introduction + "SAVED VACANCY\n\n" + _readable_fields(details) +
            "\n\nVacancy description:\n" + description +
            "\n\nYOUR VERIFIED PROFILE\n\n" + _readable_fields(profile) +
            "\n\nSELECTED REFERENCE DOCUMENTS\n\n" + selected +
            "\n\nJOB-SPECIFIC SUPPORTING SOURCES\n\n" + (_readable_fields(supporting) if supporting else 'None included.') +
            "\n\nWRITING INSTRUCTIONS\n\n" + system + "\n\n" + task
        )

    def check_codex(self) -> dict:
        """Check connection only after the user requests it, never on startup."""
        from .codex_client import codex_status

        return codex_status()

    def ai_settings(self) -> dict:
        from .providers import load_settings
        return load_settings(self.root)

    def save_ai_settings(self, settings: dict) -> dict:
        from .providers import save_settings
        return save_settings(self.root, settings)

    def generate(self, job_id: str, reference_ids: list[str],
                 output_root: Path | None = None, compile_pdf: bool = False,
                 provider_settings: dict | None = None) -> dict:
        return self._documents(job_id, reference_ids, output_root,
                               dry_run=False, compile_pdf=compile_pdf, provider_settings=provider_settings)

    def _draft_directory(self, directory: Path) -> Path:
        """Constrain document actions to saved application folders without links."""
        base = self.root.resolve() / "applications"
        path = Path(directory).resolve()
        if path == base or not path.is_relative_to(base):
            raise ValueError("Choose a saved draft inside Job Radar's applications folder.")
        if not path.is_dir():
            raise ValueError("The saved draft folder no longer exists.")
        if is_incomplete(path):
            raise ValueError("This generation is incomplete or was aborted. Start a new run to create a completed application.")
        original = Path(directory).absolute()
        if not original.is_relative_to(base):
            raise ValueError("Choose a saved draft inside Job Radar's applications folder.")
        for candidate in (original, *original.parents):
            if _linked(candidate):
                raise ValueError("Linked draft folders cannot be used.")
            if candidate == base:
                break
        return path

    def _saved_tailoring(self, directory: Path) -> TailoredApplication:
        if (directory / "VALIDATION_FAILED.txt").exists():
            raise ValueError("This draft did not pass validation. Choose a completed draft.")
        for name in ("cv.tex", "motivation_letter.tex"):
            path = directory / name
            if not path.is_file() or _linked(path):
                raise ValueError("Choose a completed CV and cover-letter draft.")
        return TailoredApplication.model_validate(_saved_json(directory, "tailoring.json"))

    def list_drafts(self, job_id: str | None = None, *, include_history: bool = False) -> list[dict]:
        """List completed saved versions without reading today's private inputs."""
        base = self.root.resolve() / "applications"
        if not base.is_dir() or _linked(base):
            return []
        drafts = []
        visited = 0
        for folder, dirs, files in os.walk(base, followlinks=False):
            visited += 1
            if visited > 5000:
                break
            directory = Path(folder)
            dirs[:] = sorted(name for name in dirs
                             if name != ".pending" and (include_history or name != ".history")
                             and not _linked(directory / name))
            if len(directory.relative_to(base).parts) >= 6:
                dirs[:] = []
            if "tailoring.json" not in files or directory == base:
                continue
            try:
                directory = self._draft_directory(directory)
                self._saved_tailoring(directory)
                try:
                    context = _saved_context(directory)
                    job = context["job"]
                except (OSError, ValueError, TypeError):
                    job = {}
                    context = {}
                identifier = context.get("job_id") if isinstance(context, dict) else None
                if not isinstance(identifier, str) or not identifier:
                    identifier = None
                if (directory / "generation_inputs.json").exists():
                    recorded = _saved_json(directory, "generation_inputs.json").get("job_id")
                    if identifier is None and isinstance(recorded, str) and recorded:
                        identifier = recorded
                if identifier is None and all(isinstance(job.get(key), str) and job[key]
                                               for key in ("company", "title", "location")):
                    identifier = stable_job_id(job["company"], job["title"], job["location"])
                if job_id is not None and identifier != job_id:
                    continue
                created = datetime.fromtimestamp((directory / "tailoring.json").stat().st_mtime).isoformat(
                    timespec="seconds"
                )
                is_revision = (directory / "revision.json").exists()
                if is_revision:
                    revision = _saved_json(directory, "revision.json")
                    if revision.get("status") != "validated":
                        continue
                company = job.get("company") if isinstance(job.get("company"), str) else ""
                title = job.get("title") if isinstance(job.get("title"), str) else ""
                name = " — ".join(part for part in (company, title) if part) or directory.name
                kind = ("Manual edit" if is_revision and revision.get('author') == 'user'
                        else "Revision" if is_revision else "Draft")
                archived = ".history" in directory.relative_to(base).parts
                if archived:
                    kind = "History · " + kind
                drafts.append({"directory": directory, "archived": archived, "label": f"{name} · {created.replace('T', ' ')} · {kind}",
                               "job_id": identifier, "company": company, "title": title,
                               "created": created, "is_revision": is_revision})
            except (OSError, ValueError, TypeError):
                # Incomplete runs and damaged snapshots should not hide good drafts.
                continue
        return sorted(drafts, key=lambda item: (item["created"], str(item["directory"])), reverse=True)

    def draft_text(self, directory: Path, document: str) -> str:
        """Read the saved version's words; this is not a rendered PDF preview."""
        from .cv import materialize_cv_context

        _document_name(document)
        directory = self._draft_directory(directory)
        tailoring = self._saved_tailoring(directory)
        self._verify_saved_wording(directory, document)
        context = _saved_context(directory)
        profile = dict(context["profile"])
        if document == "cover_letter":
            letter = tailoring.motivation_letter
            blocks = [letter.subject]
            recipient = "\n".join(value for value in (letter.recipient_name, letter.recipient_role,
                                                        context["job"].get("company")) if value)
            if recipient and letter.show_recipient_block:
                blocks.append(recipient)
            blocks.extend((letter.salutation, *letter.paragraphs, letter.closing))
            name = profile.get("contact", {}).get("name")
            if name:
                blocks.append(name)
            return "\n\n".join(blocks)
        # Older request snapshots omitted interests; never fill them from a
        # subsequently edited master profile or invent a substitute section.
        legacy_note = ""
        if "interests" not in profile:
            profile["interests"] = []
            legacy_note = ("\n\nThis older snapshot did not retain the interests section. "
                           "Open the generated document for its full content.")
        try:
            selected = materialize_cv_context(tailoring, profile)
        except (KeyError, TypeError) as exc:
            raise ValueError("The saved profile cannot reconstruct this CV. Open its generated file.") from exc
        return (_plain_cv(selected) + legacy_note +
                "\n\nText view: this shows the saved generated profile and selected facts. "
                "Fixed template text (such as Additional information) and page layout "
                "are visible in the generated document.")

    @staticmethod
    def _verify_saved_wording(directory: Path, document: str) -> None:
        """Do not present the structured snapshot as a later manually edited file."""
        manifest = directory / "document_hashes.json"
        if not manifest.exists() and not manifest.is_symlink():
            return
        hashes = _saved_json(directory, "document_hashes.json")
        name = "cv.tex" if document == "cv" else "motivation_letter.tex"
        path = directory / name
        if _linked(path) or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("The saved document is unavailable or too large to review.")
        expected = hashes.get(name)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not isinstance(expected, str) or actual != expected:
            raise ValueError(
                f"{name} was edited after generation, or its saved version cannot be verified. "
                "This text view uses the original generated wording and cannot show those edits. "
                "Open the generated file to read the current document; your edits are preserved."
            )

    def compare_draft(self, directory: Path, document: str) -> str:
        """Compare saved wording with its immediate predecessor inside this app."""
        label = _document_name(document)
        directory = self._draft_directory(directory)
        self._saved_tailoring(directory)
        marker = directory / "revision.json"
        if not marker.exists() and not marker.is_symlink():
            raise ValueError("This is an original draft. It has no previous version to compare.")
        revision = _saved_json(directory, "revision.json")
        if (revision.get("schema_version") != 1 or revision.get("status") != "validated" or
                not isinstance(revision.get("source_dir"), str) or not revision["source_dir"]):
            raise ValueError("This draft does not have a valid saved previous-version record.")
        source = self._draft_directory(resolve_previous_source(
            Path(revision["source_dir"]), revision.get("source_hashes", {}),
            self.root / "applications"))
        if source == directory:
            raise ValueError("The previous-version record points to this same draft.")
        self._saved_tailoring(source)
        hashes = revision.get("source_hashes", {})
        if not isinstance(hashes, dict):
            raise ValueError("The previous-version record has invalid saved file hashes.")
        name = "cv.tex" if document == "cv" else "motivation_letter.tex"
        for filename in ("tailoring.json", name):
            expected = hashes.get(filename)
            if expected is None:
                continue
            path = source / filename
            if _linked(path) or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("The previous document is unavailable or too large to compare.")
            if not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(
                    "The previous version changed after this revision was created. "
                    "Open the saved files to review them; a reliable wording comparison is unavailable."
                )
        previous = self.draft_text(source, document)
        current = self.draft_text(directory, document)
        changes = "\n".join(difflib.unified_diff(
            previous.splitlines(), current.splitlines(),
            fromfile=f"Previous {label}", tofile=f"Selected {label}", lineterm="",
        ))
        if not changes:
            return f"No wording changes in this {label.lower()} compared with the previous version."
        return "Lines beginning with - were removed; lines beginning with + were added.\n\n" + changes

    def improve(self, source_dir: Path, document: str, feedback: str,
                compile_pdf: bool = False, provider_settings: dict | None = None,
                formatting_only: bool = False) -> dict:
        """Update the selected files after validation, retaining their prior state in history."""
        from .revisions import revise_application

        _document_name(document)
        source_dir = self._editable_draft_directory(source_dir)
        self._saved_tailoring(source_dir)
        if not isinstance(feedback, str) or not feedback.strip():
            raise ValueError("Describe what you would like to change.")
        before = source_fingerprint(source_dir)
        options = {"formatting_only": True} if formatting_only else {}
        directory = revise_application(source_dir=source_dir, feedback=feedback,
                                       document=document, output_root=self.root / "applications" / ".pending",
                                       compile_pdf=compile_pdf, backend="codex",
                                       provider_settings=provider_settings if provider_settings is not None else self.ai_settings(),
                                       **options)
        return self._finish_document_edit(source_dir, directory, before)

    def manual_document(self, source_dir: Path, document: str) -> dict:
        from .manual_edits import load_manual_document
        return load_manual_document(self._draft_directory(source_dir), document)

    def save_manual_document(self, source_dir: Path, document: str, values: dict,
                             notes: str, expected_fingerprint: str, compile_pdf: bool = False) -> dict:
        from .manual_edits import save_manual_document
        source_dir = self._editable_draft_directory(source_dir)
        before = source_fingerprint(source_dir)
        directory = save_manual_document(source_dir=source_dir, document=document,
                                         values=values, notes=notes, expected_fingerprint=expected_fingerprint,
                                         output_root=self.root / "applications" / ".pending",
                                         compile_pdf=compile_pdf)
        return self._finish_document_edit(source_dir, directory, before)

    def _editable_draft_directory(self, directory: Path) -> Path:
        directory = self._draft_directory(directory)
        if any(part in {".history", ".pending"} for part in directory.relative_to(self.root.resolve() / "applications").parts):
            raise ValueError("Previous versions are read-only. Select the current application to make changes.")
        return directory

    def _finish_document_edit(self, source: Path, staged: Path, before: str) -> dict:
        result = self._document_result([staged], staged)
        audit = _saved_json(staged, "revision.json")
        if audit.get("status") == "no_changes":
            result.update(outputs=[source], output_root=source, unchanged=True)
            return result
        if audit.get("status") != "validated":
            return result
        active = publish_revision(source, staged, self.root / "applications", before)
        return self._document_result([active], active)

    @staticmethod
    def _document_result(outputs: list[Path], destination: Path) -> dict:
        validation_errors, compile_errors, review_notes = [], [], []
        for directory in outputs:
            for name, errors in (
                ("VALIDATION_FAILED.txt", validation_errors),
                ("COMPILE_FAILED.txt", compile_errors),
                ("LETTER_COMPILE_FAILED.txt", compile_errors),
                ("REVIEW_NOTES.txt", review_notes),
            ):
                marker = directory / name
                if marker.is_file():
                    errors.append(f"{directory.name}: {marker.read_text(encoding='utf-8').strip()}")
        return {"outputs": outputs, "output_root": destination,
                "validation_errors": validation_errors, "compile_errors": compile_errors,
                "review_notes": review_notes}

    def _documents(self, job_id: str, reference_ids: list[str], output_root: Path | None,
                   *, dry_run: bool, compile_pdf: bool, provider_settings: dict | None = None) -> dict:
        from .cv import generate_cvs
        from .job_context import snapshot

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("Select one saved vacancy before preparing documents.")
        if not isinstance(reference_ids, list) or any(not isinstance(item, str) for item in reference_ids):
            raise ValueError("Selected reference IDs must be a list.")
        new_run = output_root is None
        destination = Path(output_root) if output_root is not None else self.root / "applications"
        outputs = generate_cvs(
            db=self.db,
            selection_path=self.root / "data" / "current_application.yaml",
            profile_path=self.root / "data" / "master_profile.yaml",
            template_path=self.root / "templates" / "cv_default.tex.j2",
            letter_template_path=self.root / "templates" / "motivation_letter.tex.j2",
            output_root=destination,
            new_run=new_run,
            dry_run=dry_run,
            compile_pdf=compile_pdf,
            backend="codex",
            provider_settings=provider_settings if provider_settings is not None else self.ai_settings(),
            resume=False,
            job_ids_override=[job_id],
            reference_ids=list(reference_ids),
            reference_library=self.reference_library,
            offline=True,
            supporting_contexts={job_id: snapshot(self.root, job_id)},
        )
        return self._document_result(outputs, outputs[0] if new_run and outputs else destination)
