"""Portable output paths and compatibility with previously saved folder names."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_radar.config import ROOT
from job_radar.cv import _application_directory, generate_cvs
from job_radar.normalize import slugify


JOB = {
    "company": "Example Medicines Laboratory",
    "title": "Senior Scientist Translational Research and Clinical Development",
}
JOB_ID = "synthetic-vacancy-one"
LONGEST_ARTIFACT = "motivation_letter_compile.log"


def _hash(job_id=JOB_ID):
    return hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]


def _old_name(kind, job_id=JOB_ID):
    label = f"{JOB['company']}-{JOB['title']}"
    if kind == "slug_only":
        return slugify(label)
    return slugify(label, max_length=48) + "-" + _hash(job_id)


def _virtual_parent(length):
    # No directories are created: exercise exact absolute lengths independently
    # of the machine's temporary-folder location and classic Windows limits.
    anchor = Path(Path.cwd().anchor)
    prefix = str(anchor / "synthetic-output-")
    return Path(prefix + "x" * (length - len(prefix)))


def _write_manifest(directory, content):
    directory.mkdir(parents=True)
    (directory / "generation_inputs.json").write_text(content, encoding="utf-8")


@pytest.fixture
def no_saved_folders(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda self: False)


def test_new_folder_shortens_a_previously_261_character_letter_path(no_saved_folders):
    parent = _virtual_parent(173)
    old = parent / _old_name("hashed") / "motivation_letter.tex"
    assert len(str(old)) == 261

    directory = _application_directory(parent, JOB, JOB_ID)

    assert directory.parent == parent
    assert len(str(directory / LONGEST_ARTIFACT)) == 240
    assert len(str(directory / "motivation_letter.tex")) < 240
    assert directory.name.endswith("-" + _hash())
    assert _old_name("hashed").startswith(directory.name.rsplit("-", 1)[0])


def test_short_parent_keeps_full_descriptive_slug(no_saved_folders):
    parent = _virtual_parent(80)

    assert _application_directory(parent, JOB, JOB_ID) == parent / _old_name("hashed")


def test_deep_parent_keeps_hash_when_no_description_fits(no_saved_folders):
    parent = _virtual_parent(193)

    directory = _application_directory(parent, JOB, JOB_ID)

    assert directory.name == _hash()
    assert len(str(directory / LONGEST_ARTIFACT)) == 240


def test_parent_too_long_for_hash_has_actionable_error(no_saved_folders):
    with pytest.raises(ValueError, match="Choose a shorter output folder"):
        _application_directory(_virtual_parent(194), JOB, JOB_ID)


def test_budget_counts_windows_utf16_units_on_every_platform(no_saved_folders):
    parent = Path(str(_virtual_parent(173))[:-1] + chr(0x1F52C))

    directory = _application_directory(parent, JOB, JOB_ID)

    rendered = str(directory / LONGEST_ARTIFACT)
    assert len(rendered.encode("utf-16-le")) // 2 <= 240
    assert len(rendered.encode("utf-16-le")) // 2 == len(rendered) + 1


def test_shortened_folders_keep_distinct_postings_separate(no_saved_folders):
    parent = _virtual_parent(173)
    first = _application_directory(parent, JOB, JOB_ID)
    second = _application_directory(parent, JOB, "synthetic-vacancy-two")

    assert first != second
    assert first.name.endswith(_hash())
    assert second.name.endswith(_hash("synthetic-vacancy-two"))


@pytest.mark.parametrize("kind", ["slug_only", "hashed"])
def test_existing_verified_folder_keeps_its_original_path(tmp_path, kind):
    directory = tmp_path / _old_name(kind)
    _write_manifest(directory, json.dumps({"job_id": JOB_ID}))

    assert _application_directory(tmp_path, JOB, JOB_ID) == directory


@pytest.mark.parametrize("kind", ["slug_only", "hashed"])
def test_existing_verified_long_folder_is_not_renamed(monkeypatch, kind):
    parent = _virtual_parent(173)
    directory = parent / _old_name(kind)
    manifest = directory / "generation_inputs.json"
    original_stat = Path.stat
    monkeypatch.setattr(Path, "is_file", lambda self: self == manifest)
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs:
                        type("Stat", (), {"st_size": 100})() if self == manifest
                        else original_stat(self, **kwargs))
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: json.dumps({"job_id": JOB_ID}))

    assert _application_directory(parent, JOB, JOB_ID) == directory
    assert len(str(directory / "motivation_letter.tex")) >= 261


def test_slug_only_folder_for_another_posting_is_left_alone(tmp_path):
    directory = tmp_path / _old_name("slug_only")
    original = json.dumps({"job_id": "another-synthetic-posting"})
    _write_manifest(directory, original)

    selected = _application_directory(tmp_path, JOB, JOB_ID)

    assert selected != directory
    assert selected.name.endswith(_hash())
    assert (directory / "generation_inputs.json").read_text(encoding="utf-8") == original


def test_hashed_folder_for_wrong_identity_is_rejected(tmp_path):
    directory = tmp_path / _old_name("hashed")
    _write_manifest(directory, json.dumps({"job_id": "another-synthetic-posting"}))

    with pytest.raises(ValueError, match="belongs to another vacancy"):
        _application_directory(tmp_path, JOB, JOB_ID)


@pytest.mark.parametrize("kind", ["slug_only", "hashed"])
@pytest.mark.parametrize("content", ["{broken", "[]", "{}", '{"job_id": null}'])
def test_corrupt_existing_manifest_is_rejected(tmp_path, kind, content):
    directory = tmp_path / _old_name(kind)
    _write_manifest(directory, content)

    with pytest.raises(ValueError, match="cannot be verified"):
        _application_directory(tmp_path, JOB, JOB_ID)


def test_oversized_existing_manifest_is_not_read(tmp_path, monkeypatch):
    directory = tmp_path / _old_name("hashed")
    _write_manifest(directory, " " * (2 * 1024 * 1024 + 1))

    def unexpected_read(*args, **kwargs):
        raise AssertionError("Oversized manifest must not be read")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    with pytest.raises(ValueError, match="too large to verify"):
        _application_directory(tmp_path, JOB, JOB_ID)


def test_compact_folder_for_wrong_identity_is_rejected(monkeypatch):
    parent = _virtual_parent(173)
    selected = _application_directory(parent, JOB, JOB_ID)
    manifest = selected / "generation_inputs.json"
    original_stat = Path.stat
    monkeypatch.setattr(Path, "is_file", lambda self: self == manifest)
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs:
                        type("Stat", (), {"st_size": 100})() if self == manifest
                        else original_stat(self, **kwargs))
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs:
                        json.dumps({"job_id": "another-synthetic-posting"}))

    with pytest.raises(ValueError, match="belongs to another vacancy"):
        _application_directory(parent, JOB, JOB_ID)


def test_impossible_output_path_stops_before_model_request(tmp_path, monkeypatch):
    parent = _virtual_parent(194)
    db_path = tmp_path / "synthetic-database"
    db_path.write_text("synthetic", encoding="utf-8")
    payload = {**JOB, "description_excerpt": "A fictional vacancy for this test."}
    database = SimpleNamespace(path=db_path, get_job=lambda identifier: {
        "job_id": identifier, "payload_json": json.dumps(payload),
    })
    original_mkdir = Path.mkdir
    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs:
                        None if self == parent else original_mkdir(self, *args, **kwargs))

    def unexpected_response(**kwargs):
        raise AssertionError("An unusable output path must be rejected before a model call")

    with pytest.raises(ValueError, match="Choose a shorter output folder"):
        generate_cvs(
            db=database, selection_path=tmp_path / "unused.yaml",
            profile_path=ROOT / "examples" / "master_profile.yaml",
            template_path=ROOT / "templates" / "cv_default.tex.j2",
            output_root=parent, dry_run=False, compile_pdf=False,
            job_ids_override=[JOB_ID], offline=True, response_fn=unexpected_response,
            reference_library=tmp_path / "empty-references",
        )


@pytest.mark.parametrize("kind", ["slug_only", "hashed"])
def test_unreadable_manifest_stops_before_reuse(tmp_path, monkeypatch, kind):
    directory = tmp_path / _old_name(kind)
    _write_manifest(directory, json.dumps({"job_id": JOB_ID}))

    def denied_read(*args, **kwargs):
        raise PermissionError("Synthetic access failure")

    monkeypatch.setattr(Path, "read_text", denied_read)
    with pytest.raises(ValueError, match="cannot be verified"):
        _application_directory(tmp_path, JOB, JOB_ID)
