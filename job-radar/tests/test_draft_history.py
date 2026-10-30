"""In-place promotion preserves complete history and rejects unsafe replacements."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

import pytest

from job_radar import draft_history as history


def _hashes(directory):
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("tailoring.json", "cv.tex", "motivation_letter.tex")}


def _tree(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() if path.is_file() else None
            for path in directory.rglob("*")}


def _version(directory, wording, source=None):
    directory.mkdir(parents=True)
    (directory / "cv.tex").write_bytes(b"unchanged companion CV")
    (directory / "motivation_letter.tex").write_text(wording, encoding="utf-8")
    (directory / "tailoring.json").write_text(json.dumps({"wording": wording}), encoding="utf-8")
    (directory / "generation_context.json").write_text('{"job":{"company":"Example"}}', encoding="utf-8")
    (directory / "photo.png").write_bytes(b"synthetic photo bytes")
    hashes = _hashes(directory)
    (directory / "document_hashes.json").write_text(json.dumps({
        name: hashes[name] for name in ("cv.tex", "motivation_letter.tex")
    }), encoding="utf-8")
    if source is not None:
        (directory / "revision.json").write_text(json.dumps({
            "schema_version": 1, "status": "validated", "source_dir": str(source),
            "source_hashes": _hashes(source), "document": "cover_letter", "author": "user",
        }), encoding="utf-8")
    return directory


@pytest.fixture
def drafts(tmp_path):
    root = tmp_path / "applications"
    source = _version(root / "active", "Original letter")
    (source / "motivation_letter.pdf").write_bytes(b"old selected PDF")
    (source / "cv.pdf").write_bytes(b"unchanged companion PDF")
    (source / "extra").mkdir()
    (source / "extra" / "notes.txt").write_bytes(b"additional source notes")
    (source / "empty-directory").mkdir()
    staged = _version(root / ".pending" / "stage", "Improved letter", source)
    (staged / "cv.pdf").write_bytes((source / "cv.pdf").read_bytes())
    return root, source, staged


def test_promotion_keeps_active_path_and_complete_immutable_backup(drafts):
    root, source, staged = drafts
    before = _tree(source)
    previous_hashes = _hashes(source)
    result = history.publish_revision(source, staged, root, history.source_fingerprint(source))

    assert result == source
    assert not staged.exists()
    audit = json.loads((source / "revision.json").read_text(encoding="utf-8"))
    archive = Path(audit["source_dir"])
    assert archive.parent == root / ".history"
    assert _tree(archive) == before
    assert (source / "motivation_letter.tex").read_text(encoding="utf-8") == "Improved letter"
    assert not (source / "motivation_letter.pdf").exists()
    assert (archive / "motivation_letter.pdf").read_bytes() == b"old selected PDF"
    for name in ("cv.tex", "cv.pdf", "photo.png"):
        assert (source / name).read_bytes() == before[name]
    assert history.resolve_previous_source(source, previous_hashes, root) == archive
    assert history.resolve_previous_source(archive, previous_hashes, root) == archive
    record = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["source_dir"] == str(source)
    assert record["source_fingerprint"] == history.source_fingerprint(archive)


def test_repeated_updates_preserve_old_descendant_comparisons(drafts):
    root, source, staged = drafts
    original_hashes = _hashes(source)
    history.publish_revision(source, staged, root, history.source_fingerprint(source))
    first_archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])
    first_archive_before = _tree(first_archive)
    first_revision_hashes = _hashes(source)
    staged = _version(root / ".pending" / "second", "Second improved letter", source)
    history.publish_revision(source, staged, root, history.source_fingerprint(source))
    second_archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])

    assert first_archive != second_archive
    assert _tree(first_archive) == first_archive_before
    assert history.resolve_previous_source(source, original_hashes, root) == first_archive
    assert history.resolve_previous_source(source, first_revision_hashes, root) == second_archive
    assert json.loads((second_archive / "revision.json").read_text())["source_dir"] == str(first_archive)


@pytest.mark.parametrize("change", ["failed", "missing_letter", "wrong_hash", "other_source"])
def test_invalid_stage_never_changes_the_active_draft(drafts, change):
    root, source, staged = drafts
    fingerprint = history.source_fingerprint(source)
    before = _tree(source)
    audit_path = staged / "revision.json"
    audit = json.loads(audit_path.read_text())
    if change == "failed":
        audit["status"] = "validation_failed"
    elif change == "missing_letter":
        (staged / "motivation_letter.tex").unlink()
    elif change == "wrong_hash":
        (staged / "motivation_letter.tex").write_text("unexpected later edit", encoding="utf-8")
    else:
        audit["source_dir"] = str(root / "other")
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ValueError):
        history.publish_revision(source, staged, root, fingerprint)

    assert _tree(source) == before
    assert staged.is_dir()
    assert not (root / ".history").exists()


def test_fingerprint_detects_recursive_files_and_empty_directories(drafts):
    _, source, _ = drafts
    original = history.source_fingerprint(source)
    (source / "extra" / "notes.txt").write_bytes(b"changed nested notes")
    changed = history.source_fingerprint(source)
    assert changed != original
    (source / "extra" / "new-empty").mkdir()
    assert history.source_fingerprint(source) != changed


def test_external_added_file_prevents_promotion(drafts):
    root, source, staged = drafts
    fingerprint = history.source_fingerprint(source)
    (source / "extra" / "external.txt").write_bytes(b"external user's new file")
    before = _tree(source)

    with pytest.raises(ValueError, match="active draft changed"):
        history.publish_revision(source, staged, root, fingerprint)

    assert _tree(source) == before
    assert staged.is_dir()
    assert not (root / ".history").exists()


def test_source_is_checked_again_immediately_before_renaming(drafts, monkeypatch):
    root, source, staged = drafts
    expected = history.source_fingerprint(source)
    original_audit = (staged / "revision.json").read_bytes()
    reserve = history._reserve_archive

    def concurrent_edit(*args):
        archive = reserve(*args)
        (source / "extra" / "during-request.txt").write_bytes(b"preserve concurrent edit")
        return archive

    monkeypatch.setattr(history, "_reserve_archive", concurrent_edit)
    with pytest.raises(ValueError, match="active draft changed"):
        history.publish_revision(source, staged, root, expected)

    assert (source / "extra" / "during-request.txt").read_bytes() == b"preserve concurrent edit"
    assert (source / "motivation_letter.tex").read_text() == "Original letter"
    assert (staged / "revision.json").read_bytes() == original_audit
    assert not any(path.is_dir() for path in (root / ".history").iterdir())


def test_failed_promotion_restores_original_and_keeps_staging_retryable(drafts, monkeypatch):
    root, source, staged = drafts
    before = _tree(source)
    staged_before = _tree(staged)
    move = history._move_directory

    def fail_promotion(start, destination):
        if start == staged:
            raise PermissionError("synthetic locked document")
        move(start, destination)

    monkeypatch.setattr(history, "_move_directory", fail_promotion)
    with pytest.raises(ValueError, match="original was restored"):
        history.publish_revision(source, staged, root, history.source_fingerprint(source))

    assert _tree(source) == before
    assert _tree(staged) == staged_before
    assert not any(path.is_dir() for path in (root / ".history").iterdir())


def test_rollback_failure_reports_preserved_original_and_staged_paths(drafts, monkeypatch):
    root, source, staged = drafts
    before = _tree(source)
    move = history._move_directory

    def obstruct_promotion(start, destination):
        if start == staged:
            source.mkdir()
            (source / "new-external-file").write_bytes(b"do not overwrite this new folder")
            raise PermissionError("synthetic promotion failure")
        move(start, destination)

    monkeypatch.setattr(history, "_move_directory", obstruct_promotion)
    with pytest.raises(RuntimeError, match="could not be restored automatically") as error:
        history.publish_revision(source, staged, root, history.source_fingerprint(source))

    archive = Path(json.loads((staged / "revision.json").read_text())["source_dir"])
    assert _tree(archive) == before
    assert staged.is_dir()
    assert (source / "new-external-file").read_bytes() == b"do not overwrite this new folder"
    assert str(archive) in str(error.value)
    assert str(staged) in str(error.value)
    assert str(source) in str(error.value)


def test_history_name_collision_does_not_replace_existing_directory(drafts, monkeypatch):
    root, source, staged = drafts
    existing = root / ".history" / ("a" * 12)
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_bytes(b"existing backup")
    ids = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(history, "uuid4", lambda: SimpleNamespace(hex=next(ids)))

    history.publish_revision(source, staged, root, history.source_fingerprint(source))

    assert (existing / "keep.txt").read_bytes() == b"existing backup"
    assert (root / ".history" / ("b" * 12)).is_dir()


def test_ambiguous_archives_are_not_chosen_arbitrarily(drafts):
    root, source, staged = drafts
    expected = _hashes(source)
    history.publish_revision(source, staged, root, history.source_fingerprint(source))
    archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])
    duplicate = archive.parent / ("e" * 12 if archive.name != "e" * 12 else "f" * 12)
    shutil.copytree(archive, duplicate)
    duplicate.with_suffix(".json").write_bytes(archive.with_suffix(".json").read_bytes())

    with pytest.raises(ValueError, match="More than one saved backup"):
        history.resolve_previous_source(source, expected, root)


def test_changed_backup_is_not_used_even_when_document_hashes_still_match(drafts):
    root, source, staged = drafts
    expected = _hashes(source)
    history.publish_revision(source, staged, root, history.source_fingerprint(source))
    archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])
    (archive / "extra" / "notes.txt").write_bytes(b"modified backup asset")

    with pytest.raises(ValueError, match="no matching saved backup"):
        history.resolve_previous_source(source, expected, root)


def test_legacy_empty_hashes_allow_existing_source_but_no_archive_fallback(drafts):
    root, source, staged = drafts
    assert history.resolve_previous_source(source, {}, root) == source
    history.publish_revision(source, staged, root, history.source_fingerprint(source))
    source.rename(root / "elsewhere")

    with pytest.raises(ValueError, match="no hashes"):
        history.resolve_previous_source(source, {}, root)


@pytest.mark.parametrize("hashes", [{"../outside": "a" * 64}, {"cv.tex": "invalid"}, []])
def test_unsafe_hash_records_are_rejected(drafts, hashes):
    root, source, _ = drafts
    with pytest.raises(ValueError, match="invalid saved file hashes"):
        history.resolve_previous_source(source, hashes, root)


def test_publish_rejects_nested_and_outside_folders(drafts, tmp_path):
    root, source, staged = drafts
    before = _tree(source)
    fingerprint = history.source_fingerprint(source)
    for bad_stage in (source, source / "extra", tmp_path):
        with pytest.raises(ValueError):
            history.publish_revision(source, bad_stage, root, fingerprint)
    assert _tree(source) == before
    assert staged.is_dir()


def test_reserved_history_cannot_be_modified_as_an_active_application(drafts):
    root, source, staged = drafts
    fingerprint = history.source_fingerprint(source)
    history.publish_revision(source, staged, root, fingerprint)
    archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])
    next_stage = _version(root / ".pending" / "next", "next letter", archive)

    with pytest.raises(ValueError, match="read-only"):
        history.publish_revision(archive, next_stage, root, history.source_fingerprint(archive))


def test_reparse_folder_is_rejected_before_reading_contents(drafts, monkeypatch):
    _, source, _ = drafts
    original_lstat = Path.lstat

    def reparse_info(path, *args, **kwargs):
        if path == source:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=1024)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse_info)
    with pytest.raises(ValueError, match="Linked folders"):
        history.source_fingerprint(source)


def test_filesystem_root_is_not_an_application_root(drafts):
    _, source, staged = drafts
    with pytest.raises(ValueError, match="filesystem root"):
        history.publish_revision(source, staged, Path(source.anchor), history.source_fingerprint(source))


@pytest.mark.parametrize("document", ["cv", "cover_letter"])
def test_publication_preserves_external_companion_edits_and_their_original_hash(drafts, document):
    root, source, staged = drafts
    selected = "cv.tex" if document == "cv" else "motivation_letter.tex"
    companion = "motivation_letter.tex" if document == "cv" else "cv.tex"
    manifest = json.loads((source / "document_hashes.json").read_text())
    original_companion_hash = manifest[companion]
    external_bytes = b"External manual formatting must remain protected"
    (source / companion).write_bytes(external_bytes)
    (staged / companion).write_bytes(external_bytes)
    (staged / selected).write_bytes(b"Revised selected document")
    staged_manifest = {selected: history._file_hash(staged / selected),
                       companion: original_companion_hash}
    (staged / "document_hashes.json").write_text(json.dumps(staged_manifest), encoding="utf-8")
    audit = json.loads((staged / "revision.json").read_text())
    audit.update(document=document, source_hashes=_hashes(source))
    (staged / "revision.json").write_text(json.dumps(audit), encoding="utf-8")
    before = _tree(source)

    history.publish_revision(source, staged, root, history.source_fingerprint(source))

    assert (source / companion).read_bytes() == external_bytes
    assert json.loads((source / "document_hashes.json").read_text())[companion] == original_companion_hash
    archive = Path(json.loads((source / "revision.json").read_text())["source_dir"])
    assert _tree(archive) == before


@pytest.mark.parametrize("change", ["different_bytes", "reset_external_hash"])
def test_publication_does_not_lose_or_reclassify_external_companion_edits(drafts, change):
    root, source, staged = drafts
    companion = "cv.tex"
    external_bytes = b"External formatting to retain"
    (source / companion).write_bytes(external_bytes)
    (staged / companion).write_bytes(external_bytes)
    manifest = json.loads((staged / "document_hashes.json").read_text())
    if change == "different_bytes":
        (staged / companion).write_bytes(b"Replacement would lose manual edits")
    manifest[companion] = history._file_hash(staged / companion)
    (staged / "document_hashes.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = _tree(source)

    with pytest.raises(ValueError, match="companion"):
        history.publish_revision(source, staged, root, history.source_fingerprint(source))

    assert _tree(source) == before
    assert staged.is_dir()
    assert not (root / ".history").exists()
