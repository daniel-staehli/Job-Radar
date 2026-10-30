"""Publish a staged draft in place while retaining immutable prior snapshots."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


_HISTORY = ".history"
_RESERVED_SOURCES = {_HISTORY, ".pending"}
_HASH = re.compile(r"[0-9a-f]{64}")
_ARCHIVE_ID = re.compile(r"[0-9a-f]{12}")


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _directory(path: Path, root: Path | None = None, *, must_exist: bool = True) -> Path:
    path = _absolute(path)
    if root is not None and (path == root or not path.is_relative_to(root)):
        raise ValueError("Choose a draft folder inside the applications folder.")
    for parent in reversed((path, *path.parents)):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if _linked(info):
            raise ValueError(f"Linked folders cannot be used for draft history: {parent}")
    if must_exist and not path.is_dir():
        raise ValueError(f"The saved draft folder is unavailable: {path}")
    if not must_exist and path.exists() and not path.is_dir():
        raise ValueError(f"The saved draft path is not a folder: {path}")
    return path


def _applications_root(path: Path) -> Path:
    root = _directory(path)
    if root == Path(root.anchor):
        raise ValueError("Use the application's own applications folder, not a filesystem root.")
    return root


def _file_hash(path: Path) -> str:
    before = path.lstat()
    if _linked(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Linked or special files cannot be used for draft history: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.lstat()
    if _linked(after) or any(getattr(before, key) != getattr(after, key) for key in
                             ("st_dev", "st_ino", "st_size", "st_mtime_ns")):
        raise ValueError("The saved draft changed while it was being checked. Try again.")
    return digest.hexdigest()


def source_fingerprint(source_dir: Path) -> str:
    """Fingerprint every file and directory, rejecting links and special files."""
    source = _directory(source_dir)
    entries = []

    def visit(directory):
        entries.append((directory.relative_to(source).as_posix(), "directory"))
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            info = child.lstat()
            if _linked(info):
                raise ValueError(f"Linked files or folders cannot be used for draft history: {child}")
            if stat.S_ISDIR(info.st_mode):
                visit(child)
            elif stat.S_ISREG(info.st_mode):
                entries.append((child.relative_to(source).as_posix(), _file_hash(child)))
            else:
                raise ValueError(f"Special files cannot be used for draft history: {child}")

    try:
        visit(source)
    except OSError as exc:
        raise ValueError("The saved draft could not be checked or changed during the check. Try again.") from exc
    data = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"The saved history record is missing: {path}") from exc
    if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise ValueError(f"The saved history record cannot be verified: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"The saved history record cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"The saved history record is invalid: {path}")
    return value


def _exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _reserve_archive(root: Path, source: Path, fingerprint: str) -> Path:
    history = _directory(root / _HISTORY, root, must_exist=False)
    history.mkdir(exist_ok=True)
    metadata = {
        "schema_version": 1, "source_dir": str(source), "source_fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    for _ in range(5):
        archive = history / uuid4().hex[:12]
        if _exists(archive):
            continue
        record = history / (archive.name + ".json")
        try:
            with record.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
        except FileExistsError:
            continue
        return archive
    raise ValueError("Could not reserve a unique history folder. Try saving the draft again.")


def _move_directory(source: Path, destination: Path) -> None:
    if _exists(destination):
        raise FileExistsError(f"The destination already exists and was left unchanged: {destination}")
    source.rename(destination)


def publish_revision(source_dir: Path, staged_dir: Path, applications_root: Path,
                     expected_fingerprint: str) -> Path:
    """Replace the active directory, archiving its exact previous contents first."""
    root = _applications_root(applications_root)
    source = _directory(source_dir, root)
    staged = _directory(staged_dir, root)
    if source.relative_to(root).parts[0] in _RESERVED_SOURCES:
        raise ValueError("Choose an active application folder; saved history and pending drafts are read-only.")
    if staged.relative_to(root).parts[0] == _HISTORY:
        raise ValueError("A saved history folder cannot be published as a staged revision.")
    if source == staged or source.is_relative_to(staged) or staged.is_relative_to(source):
        raise ValueError("The active and staged drafts must be separate, non-nested folders.")
    if not isinstance(expected_fingerprint, str) or not _HASH.fullmatch(expected_fingerprint):
        raise ValueError("The original draft fingerprint is invalid. Reopen the draft and try again.")
    source_fingerprint(staged)  # Validate the complete staging tree before touching history.
    audit_path = staged / "revision.json"
    audit = _read_json(audit_path)
    if audit.get("status") != "validated":
        raise ValueError("Only a validated staged revision can replace the active draft.")
    if not isinstance(audit.get("source_dir"), str) or _absolute(audit["source_dir"]) != source:
        raise ValueError("The staged revision belongs to a different source draft.")
    for name in ("cv.tex", "motivation_letter.tex", "tailoring.json", "generation_context.json"):
        if not (staged / name).is_file():
            raise ValueError(f"The staged revision is incomplete: {name} is missing.")
    document_files = {"cv": "cv.tex", "cover_letter": "motivation_letter.tex"}
    document = audit.get("document")
    if document not in document_files:
        raise ValueError("The staged revision does not identify the document being changed.")
    selected = document_files[document]
    companion = document_files["cover_letter" if document == "cv" else "cv"]
    document_hashes = _read_json(staged / "document_hashes.json")
    if document_hashes.get(selected) != _file_hash(staged / selected):
        raise ValueError("The staged document files do not match their saved hashes.")
    # An externally edited companion is copied exactly, but deliberately keeps
    # its original generated hash so later edits cannot silently overwrite it.
    # Validate the copy against the source bytes, not that older generated hash.
    companion_hash = _file_hash(source / companion)
    if _file_hash(staged / companion) != companion_hash:
        raise ValueError("The unselected companion document changed in the staged revision.")
    source_manifest = source / "document_hashes.json"
    source_hashes = _read_json(source_manifest) if _exists(source_manifest) else {}
    expected_companion = source_hashes.get(companion) or companion_hash
    if (not isinstance(expected_companion, str) or not _HASH.fullmatch(expected_companion)
            or document_hashes.get(companion) != expected_companion):
        raise ValueError("The staged companion document must retain its original saved hash.")
    if source_fingerprint(source) != expected_fingerprint:
        raise ValueError("The active draft changed while the revision was being prepared. Reopen it and try again.")

    archive = _reserve_archive(root, source, expected_fingerprint)
    original_audit = audit_path.read_bytes()
    archived = False
    published = False
    try:
        audit["source_dir"] = str(archive)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        source_fingerprint(staged)
        # Check again immediately before either rename: a model request may take minutes.
        if source_fingerprint(source) != expected_fingerprint:
            raise ValueError("The active draft changed while the revision was being prepared. Reopen it and try again.")
        _move_directory(source, archive)
        archived = True
        try:
            _move_directory(staged, source)
        except Exception as promotion_error:
            try:
                _move_directory(archive, source)
                archived = False
            except Exception as rollback_error:
                raise RuntimeError(
                    "The revised draft could not be installed and the original could not be restored automatically. "
                    f"Original files are preserved at {archive}. Revised files remain at {staged}. "
                    f"The active path is {source}. Restore the original folder before continuing. "
                    f"Restoration error: {rollback_error}"
                ) from promotion_error
            raise ValueError(
                f"The revised draft could not be installed. The original was restored at {source}; "
                f"the staged revision remains at {staged}. Close any open document and try again."
            ) from promotion_error
        published = True
        return source
    finally:
        if not published and not archived:
            # Leave a failed staging run retryable; never alter an archived original.
            try:
                audit_path.write_bytes(original_audit)
            except OSError:
                pass


def _validated_hashes(expected_hashes: dict) -> dict:
    if not isinstance(expected_hashes, dict):
        raise ValueError("The previous-version record has invalid saved file hashes.")
    for name, value in expected_hashes.items():
        if (not isinstance(name, str) or not name or name in {".", ".."}
                or "/" in name or "\\" in name or ":" in name
                or not isinstance(value, str) or not _HASH.fullmatch(value)):
            raise ValueError("The previous-version record has invalid saved file hashes.")
    return expected_hashes


def _matches(directory: Path, hashes: dict) -> bool:
    try:
        return all(_file_hash(directory / name) == value for name, value in hashes.items())
    except FileNotFoundError:
        return False


def resolve_previous_source(recorded_dir: Path, expected_hashes: dict,
                            applications_root: Path) -> Path:
    """Find a recorded predecessor, including its archive after an in-place update."""
    root = _applications_root(applications_root)
    recorded = _directory(recorded_dir, root, must_exist=False)
    hashes = _validated_hashes(expected_hashes)
    if recorded.is_dir() and (not hashes or _matches(recorded, hashes)):
        return recorded
    if not hashes:
        raise ValueError("The previous draft is unavailable and its record has no hashes for finding a backup.")
    history = _directory(root / _HISTORY, root, must_exist=False)
    matches = []
    if history.is_dir():
        for record in sorted(history.glob("*.json")):
            if not _ARCHIVE_ID.fullmatch(record.stem):
                continue
            metadata = _read_json(record)
            if (metadata.get("schema_version") != 1
                    or not isinstance(metadata.get("source_dir"), str)
                    or _absolute(metadata["source_dir"]) != recorded):
                continue
            archive = _directory(history / record.stem, root, must_exist=False)
            if (archive.is_dir() and _matches(archive, hashes)
                    and source_fingerprint(archive) == metadata.get("source_fingerprint")):
                matches.append(archive)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("More than one saved backup matches the previous draft. Choose the previous version explicitly.")
    raise ValueError("The previous draft changed or is unavailable, and no matching saved backup could be verified.")
