"""Local, versioned human edits to saved application wording. No model calls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re

from .config import ROOT
from .cv import compile_latex, latex_environment, latex_escape, validate_tailoring
from .models import TailoredApplication
from .output_paths import create_application_directory
from .revisions import (
    DOCUMENT_FILES, _json, _read, _render_context, _render_profile, _write_json,
    load_generation_context,
)

MAX_TEXT_CHARS = 20_000
MAX_NOTES_CHARS = 12_000


def manual_validation_errors(errors: list[str]) -> tuple[list[str], list[str]]:
    """Human wording may depart from writing preferences, never fact-ID rules."""
    preferences = (
        "Profile is longer than", "Motivation letter has",
        "Generated prose contains an em/en dash", "German motivation letter contains ß",
        "Named recipient is not present in the supplied job text:",
    )
    review = [error for error in errors if error.startswith(preferences)]
    return [error for error in errors if error not in review], review


def validate_revision_tailoring(tailoring: TailoredApplication, context: dict,
                               selected_document: str | None = None) -> tuple[list[str], list[str]]:
    """Keep human preferences for the companion; validate new AI wording strictly."""
    if selected_document is not None and selected_document not in DOCUMENT_FILES:
        raise ValueError("Choose the CV or the cover letter.")
    manual_documents = context.get("manual_documents", [])
    if not isinstance(manual_documents, list) or any(not isinstance(document, str) or document not in DOCUMENT_FILES for document in manual_documents):
        raise ValueError("The saved manual-edit history is invalid. Choose an earlier complete version.")
    blocking, review = [], []
    for document in DOCUMENT_FILES:
        errors = validate_tailoring(tailoring, context["profile"], context["full_description"], document=document)
        if document in manual_documents and document != selected_document:
            errors, notes = manual_validation_errors(errors)
            review.extend(notes)
        blocking.extend(errors)
    return list(dict.fromkeys(blocking)), list(dict.fromkeys(review))


def _source(source_dir: Path, document: str) -> tuple[dict, dict, dict, dict, str]:
    if document not in DOCUMENT_FILES:
        raise ValueError("Choose the CV or the cover letter to edit.")
    source_dir = Path(source_dir).resolve()
    if (source_dir / "VALIDATION_FAILED.txt").exists():
        raise ValueError("Choose a completed draft that passed validation.")
    context = load_generation_context(source_dir)
    contents = {name: _read(source_dir / name) for name in
                ("tailoring.json", "cv.tex", "motivation_letter.tex")}
    tailoring = TailoredApplication.model_validate_json(contents["tailoring.json"]).model_dump()
    has_manifest = (source_dir / "document_hashes.json").exists()
    manifest = _json(source_dir / "document_hashes.json") if has_manifest else {}
    selected = DOCUMENT_FILES[document]
    if has_manifest and (not isinstance(manifest, dict) or
                     manifest.get(selected) != hashlib.sha256(contents[selected]).hexdigest()):
        raise ValueError("This document was changed outside Job Radar. Open its existing file to preserve those edits; the text editor cannot safely reconstruct them.")
    saved_notes = _json(source_dir / "manual_notes.json") if (source_dir / "manual_notes.json").exists() else {}
    fingerprint_data = {"context": context, "notes": saved_notes, "hashes": {
        name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}, "manifest": manifest}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return context, tailoring, contents, manifest, fingerprint


def _get(root: dict, path: tuple):
    result = root
    for key in path:
        result = result[key]
    return result


def _put(root: dict, path: tuple, value):
    target = root
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _fields(context: dict, tailoring: dict, document: str) -> list[dict]:
    """Expose only existing, rendered fields; never IDs, templates or file paths."""
    fields = []
    roots = {"profile": context["profile"], "tailoring": tailoring}

    def add(root, path, label, kind="text", optional=False):
        value = _get(roots[root], path)
        if not isinstance(value, (str, list)) and value is not None:
            return
        if kind == "paragraphs":
            value = "\n\n".join(value)
        elif kind == "lines":
            value = "\n".join(value)
        fields.append({"id": f"field-{len(fields)}", "label": label, "value": value or "",
                       "multiline": kind != "short", "root": root, "path": path,
                       "kind": kind, "optional": optional})

    if document == "cover_letter":
        for key, label in (("subject", "Subject"), ("recipient_name", "Recipient name"),
                           ("recipient_role", "Recipient role"), ("salutation", "Salutation")):
            add("tailoring", ("motivation_letter", key), label, "short", key.startswith("recipient_"))
        add("tailoring", ("motivation_letter", "paragraphs"), "Letter body (3–6 paragraphs, separated by blank lines)", "paragraphs")
        add("tailoring", ("motivation_letter", "closing"), "Closing", "short")
        return fields

    add("tailoring", ("headline_tags",), "Headline (one phrase per line)", "lines")
    add("tailoring", ("profile",), "Profile")
    profile = context["profile"]
    # Contact fields are shared by both templates. Keep them unchanged here so
    # the untouched companion's structured text view still matches its file.
    for selected in tailoring["selected_roles"]:
        for index, role in enumerate(profile["experiences"]):
            if role["key"] != selected["role_key"]:
                continue
            title = role.get("role", role.get("title", role["key"]))
            for key in ("role", "title", "organisation", "location", "dates", "subtitle"):
                if isinstance(role.get(key), str):
                    add("profile", ("experiences", index, key), f"{title} · {key}", "short", key == "subtitle")
            for number, bullet in enumerate(role["bullets"]):
                if bullet["id"] in selected["bullet_ids"]:
                    add("profile", ("experiences", index, "bullets", number, "text"), f"{title} · bullet {number + 1}")
    for collection, selection, keys in (
        ("skills", "selected_skill_ids", ("label", "text")),
        ("publications", "selected_publication_ids", ("citation", "url")),
        ("additional_experience", "selected_additional_ids", ("role", "location", "dates", "text")),
    ):
        for index, item in enumerate(profile[collection]):
            if item["id"] not in tailoring[selection]:
                continue
            for key in keys:
                if isinstance(item.get(key), str):
                    add("profile", (collection, index, key), f"{collection.replace('_', ' ').title()} {index + 1} · {key}")
    for index, item in enumerate(profile["education"]):
        for key in ("institution", "degree", "dates", "detail"):
            if isinstance(item.get(key), str):
                add("profile", ("education", index, key), f"Education {index + 1} · {key}", optional=key == "detail")
    for key in ("languages", "interests"):
        add("profile", (key,), key.title() + " (one entry per line)", "lines", True)
    return fields


def load_manual_document(source_dir: Path, document: str) -> dict:
    context, tailoring, _, _, fingerprint = _source(source_dir, document)
    notes = ""
    notes_path = Path(source_dir) / "manual_notes.json"
    if notes_path.exists():
        saved = _json(notes_path)
        if isinstance(saved, dict) and isinstance(saved.get(document), str):
            notes = saved[document]
    return {"document": document, "notes": notes, "source_fingerprint": fingerprint,
            "fields": [{key: value for key, value in item.items() if key not in {"root", "path", "kind", "optional"}}
                       for item in _fields(context, tailoring, document)]}


def save_manual_document(*, source_dir: Path, document: str, values: dict[str, str],
                         notes: str, expected_fingerprint: str, output_root: Path,
                         compile_pdf: bool = False) -> Path:
    """Save human wording locally as a new complete, auditable application version."""
    source_dir = Path(source_dir).resolve()
    context, tailoring, source_bytes, manifest, fingerprint = _source(source_dir, document)
    if expected_fingerprint != fingerprint:
        raise ValueError("The saved draft changed while the editor was open. Reopen the editor before saving.")
    if not isinstance(notes, str) or len(notes) > MAX_NOTES_CHARS:
        raise ValueError("Keep manual notes under 12,000 characters.")
    fields = _fields(context, tailoring, document)
    if not isinstance(values, dict) or set(values) != {field["id"] for field in fields}:
        raise ValueError("The editable fields changed. Reopen the text editor before saving.")
    original_context = deepcopy(context)
    changed = []
    for field in fields:
        value = values[field["id"]]
        if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS or "\x00" in value:
            raise ValueError(f"{field['label']}: enter text under 20,000 characters.")
        if value == field["value"]:
            continue
        if not value.strip() and not field["optional"]:
            raise ValueError(f"{field['label']} cannot be empty.")
        changed.append({"field": field["label"], "before": field["value"], "after": value})
        if field["kind"] == "paragraphs":
            value = [part.strip() for part in re.split(r"\n\s*\n", value.strip()) if part.strip()]
        elif field["kind"] == "lines":
            value = [line.strip() for line in value.splitlines() if line.strip()]
        elif field["optional"] and not value.strip() and field["root"] == "tailoring":
            value = None
        _put(context["profile"] if field["root"] == "profile" else tailoring, field["path"], value)
    # These explicit human edits are allowed for this version only. No changes
    # are made to the verified master profile or any other application's inputs.
    if document == "cv":
        context["profile"]["allowed_headline_tags"] = list(dict.fromkeys(
            context["profile"]["allowed_headline_tags"] + tailoring["headline_tags"]))
    revised = TailoredApplication.model_validate(tailoring)
    errors = validate_tailoring(revised, context["profile"], context["full_description"])
    changed_text = " ".join(change["after"] for change in changed).casefold()
    for forbidden in context["profile"]["never_claim"]:
        if forbidden["pattern"].casefold() in changed_text:
            errors.append("Manual wording contains a forbidden/unsupported claim: " + forbidden["pattern"])
    blocking, review = manual_validation_errors(list(dict.fromkeys(errors)))
    if blocking:
        raise ValueError("Please review these factual issues before saving: " + "; ".join(blocking))
    manual_documents = context.get("manual_documents", [])
    if not isinstance(manual_documents, list) or any(not isinstance(item, str) or item not in DOCUMENT_FILES for item in manual_documents):
        raise ValueError("The saved manual-edit history is invalid. Choose an earlier complete version.")
    if changed:
        context["manual_documents"] = list(dict.fromkeys(manual_documents + [document]))
    selected_name = DOCUMENT_FILES[document]
    other_name = DOCUMENT_FILES["cover_letter" if document == "cv" else "cv"]
    render_profile, copied, asset_notes = _render_profile(context["profile"], source_dir)
    text = source_bytes[selected_name].decode("utf-8-sig")
    if changed:
        environment = latex_environment(ROOT / "templates")
        environment.filters["tex"] = latex_escape
        template = environment.from_string(context["templates"][document])
        text = template.render(**_render_context(revised, render_profile, context["job"], document))
    for name in (str(Path(other_name).with_suffix(".pdf")), "fit_analysis.md", "job_description.md",
                 "references_used.json", "job_resources_used.json"):
        if (source_dir / name).exists():
            copied[name] = _read(source_dir / name)
    if not changed and (source_dir / Path(selected_name).with_suffix(".pdf")).exists():
        copied[str(Path(selected_name).with_suffix(".pdf"))] = _read(source_dir / Path(selected_name).with_suffix(".pdf"))
    all_notes = _json(source_dir / "manual_notes.json") if (source_dir / "manual_notes.json").exists() else {}
    if not isinstance(all_notes, dict):
        raise ValueError("The saved manual notes cannot be read. Reopen an earlier complete version.")
    all_notes[document] = notes
    directory = create_application_directory(Path(output_root), context["job"].get("company", ""))
    (directory / selected_name).write_bytes(text.encode("utf-8") if changed else source_bytes[selected_name])
    (directory / other_name).write_bytes(source_bytes[other_name])
    _write_json(directory / "tailoring.json", revised.model_dump())
    _write_json(directory / "generation_context.json", context)
    _write_json(directory / "manual_notes.json", all_notes)
    _write_json(directory / "document_hashes.json", {
        selected_name: hashlib.sha256((directory / selected_name).read_bytes()).hexdigest(),
        other_name: manifest.get(other_name) or hashlib.sha256(source_bytes[other_name]).hexdigest(),
    })
    for name, data in copied.items():
        (directory / name).write_bytes(data)
    audit = {"schema_version": 1, "source_dir": str(source_dir), "document": document,
             "created_at": datetime.now(timezone.utc).isoformat(), "author": "user", "backend": "manual",
             "provider": "none", "model": None, "status": "validated", "feedback": notes,
             "changed_fields": changed, "review_notes": review + asset_notes,
             "source_hashes": {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()},
             "context_sha256": hashlib.sha256(json.dumps(original_context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    _write_json(directory / "revision.json", audit)
    (directory / "changes.diff").write_text("".join(difflib.unified_diff(
        source_bytes[selected_name].decode("utf-8-sig").splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile=f"previous/{selected_name}", tofile=f"manual/{selected_name}")), encoding="utf-8")
    if review or asset_notes:
        (directory / "REVIEW_NOTES.txt").write_text("\n".join(review + asset_notes) + "\n", encoding="utf-8")
    if compile_pdf and changed:
        ok, log = compile_latex(directory, selected_name)
        log_name = Path(selected_name).stem + "_compile.log"
        (directory / log_name).write_text(log, encoding="utf-8")
        if not ok:
            marker = "COMPILE_FAILED.txt" if document == "cv" else "LETTER_COMPILE_FAILED.txt"
            (directory / marker).write_text("The edited text is saved. PDF compilation failed; see " + log_name + ".\n", encoding="utf-8")
    return directory
