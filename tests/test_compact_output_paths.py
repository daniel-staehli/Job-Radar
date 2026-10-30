"""Compact GUI output names, path limits, and non-destructive allocation."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_radar import output_paths


DATE = "2026-10-17"
LONGEST_ARTIFACT = "motivation_letter_compile.log"


@pytest.fixture(autouse=True)
def fixed_date(monkeypatch):
    monkeypatch.setattr(output_paths, "datetime", SimpleNamespace(
        now=lambda: datetime(2026, 10, 17, 14, 23, 45),
    ))


def _set_ids(monkeypatch, *identifiers):
    values = iter(identifiers)
    monkeypatch.setattr(output_paths, "uuid4", lambda: SimpleNamespace(hex=next(values)))


def _virtual_parent(length):
    # These paths are never created; exact lengths do not depend on temp roots.
    anchor = Path(Path.cwd().anchor)
    prefix = str(anchor / "synthetic-output-")
    return Path(prefix + "x" * (length - len(prefix)))


def _record_creation(monkeypatch):
    calls = []
    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs:
                        calls.append((self, args, kwargs)))
    return calls


def test_folder_name_uses_company_local_date_and_six_character_id(tmp_path, monkeypatch):
    _set_ids(monkeypatch, "abc123456789")

    directory = output_paths.create_application_directory(tmp_path, "Example Research, Inc.")

    assert directory == tmp_path / f"example-research-inc_{DATE}_abc123"
    assert directory.is_dir()


def test_company_slug_is_at_most_24_characters(tmp_path, monkeypatch):
    _set_ids(monkeypatch, "abc123456789")

    directory = output_paths.create_application_directory(tmp_path, "A" * 40)

    assert directory.name == f"{'a' * 24}_{DATE}_abc123"


@pytest.mark.parametrize("company", ["", "   ", "研究企业", "!!!"])
def test_empty_ascii_slug_has_readable_company_fallback(tmp_path, monkeypatch, company):
    _set_ids(monkeypatch, "abc123456789")

    directory = output_paths.create_application_directory(tmp_path, company)

    assert directory.name == f"company_{DATE}_abc123"


def test_same_company_runs_get_separate_folders(tmp_path, monkeypatch):
    _set_ids(monkeypatch, "abc123000000", "def456000000")

    first = output_paths.create_application_directory(tmp_path, "Example")
    second = output_paths.create_application_directory(tmp_path, "Example")

    assert first != second
    assert first.is_dir() and second.is_dir()
    assert len(list(tmp_path.iterdir())) == 2


def test_collision_retries_without_touching_existing_folder(tmp_path, monkeypatch):
    first = tmp_path / f"example_{DATE}_abc123"
    first.mkdir()
    protected = first / "existing-document.txt"
    protected.write_text("Keep this draft unchanged.", encoding="utf-8")
    _set_ids(monkeypatch, "abc123000000", "def456000000")

    created = output_paths.create_application_directory(tmp_path, "Example")

    assert created == tmp_path / f"example_{DATE}_def456"
    assert created.is_dir()
    assert protected.read_text(encoding="utf-8") == "Keep this draft unchanged."


def test_exhausted_collisions_fail_without_reusing_existing_folder(tmp_path, monkeypatch):
    existing = tmp_path / f"example_{DATE}_abc123"
    existing.mkdir()
    protected = existing / "existing-document.txt"
    protected.write_bytes(b"Keep this draft unchanged.")
    attempts = []

    def duplicate_id():
        attempts.append(True)
        return SimpleNamespace(hex="abc123000000")

    monkeypatch.setattr(output_paths, "uuid4", duplicate_id)
    with pytest.raises(ValueError, match="Try generating the documents again"):
        output_paths.create_application_directory(tmp_path, "Example")

    assert len(attempts) == 5
    assert protected.read_bytes() == b"Keep this draft unchanged."
    assert list(tmp_path.iterdir()) == [existing]


def test_company_name_adapts_to_the_complete_artifact_path(monkeypatch):
    parent = _virtual_parent(180)
    calls = _record_creation(monkeypatch)
    _set_ids(monkeypatch, "abc123000000")

    directory = output_paths.create_application_directory(parent, "Example Research Laboratory")

    assert len(str(directory / LONGEST_ARTIFACT)) == 240
    assert directory.name == f"example-res_{DATE}_abc123"
    assert calls == [(directory, (), {"parents": True, "exist_ok": False})]


def test_unicode_parent_budget_counts_utf16_units_on_every_platform(monkeypatch):
    parent = Path(str(_virtual_parent(180))[:-1] + "🔬")
    _record_creation(monkeypatch)
    _set_ids(monkeypatch, "abc123000000")

    directory = output_paths.create_application_directory(parent, "Example Research Laboratory")

    assert len(str(directory / LONGEST_ARTIFACT).encode("utf-16-le")) // 2 == 240
    assert directory.name == f"example-re_{DATE}_abc123"


@pytest.mark.parametrize("company,initial", [("Example", "e"), ("研究", "c")])
def test_deepest_supported_parent_keeps_one_company_character(monkeypatch, company, initial):
    parent = _virtual_parent(190)
    _record_creation(monkeypatch)
    _set_ids(monkeypatch, "abc123000000")

    directory = output_paths.create_application_directory(parent, company)

    assert directory.name == f"{initial}_{DATE}_abc123"
    assert len(str(directory / LONGEST_ARTIFACT)) == 240


def test_too_deep_parent_is_rejected_before_creating_any_directory(monkeypatch):
    calls = _record_creation(monkeypatch)

    def unexpected_id():
        raise AssertionError("Path validation should finish before reserving a run ID")

    monkeypatch.setattr(output_paths, "uuid4", unexpected_id)
    with pytest.raises(ValueError, match="Choose a shorter output folder"):
        output_paths.create_application_directory(_virtual_parent(191), "Example")

    assert calls == []
