"""Supporting articles are local per-vacancy context, never candidate evidence."""
import gc
import hashlib
import json
from pathlib import Path
import socket
import tkinter as tk

import pytest

from job_radar import job_context as context
from job_radar.gui_job_context import JobContextDialog


JOB = "job_0123456789ab"
OTHER = "job_ext_" + "b" * 24


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Supporting references must never fetch a link")
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_empty_read_does_not_create_files(tmp_path):
    assert context.list_sources(tmp_path, JOB) == []
    assert context.snapshot(tmp_path, JOB) == {}
    assert not (tmp_path / "data").exists()


def test_edit_exclusion_and_removal_are_per_vacancy(tmp_path):
    a = context.save_source(tmp_path, JOB, {"title": "Company paper", "url": "https://example.org/paper", "notes": "Ask about this method", "text": "Their experiments tested A.", "included": True})
    context.save_source(tmp_path, OTHER, {"title": "Other paper", "notes": "Different vacancy"})
    assert context.snapshot(tmp_path, JOB)["sources"][0]["source_text"] == "Their experiments tested A."
    edited = context.save_source(tmp_path, JOB, {"title": "Revised paper", "notes": "Keep for later", "included": False}, source_id=a["id"])
    assert edited["id"] == a["id"]
    assert edited["created_at"] == a["created_at"]
    assert context.snapshot(tmp_path, JOB) == {}
    context.remove_source(tmp_path, JOB, a["id"])
    assert context.list_sources(tmp_path, JOB) == []
    assert len(context.list_sources(tmp_path, OTHER)) == 1


def test_link_only_does_not_claim_reading_or_candidate_evidence(tmp_path):
    context.save_source(tmp_path, JOB, {"title": "Linked study", "url": "https://example.org/article"})
    result = context.snapshot(tmp_path, JOB)
    assert result["sources"][0]["source_text"] == ""
    assert "have not been read" in result["sources"][0]["text_status"]
    assert "not evidence" in result["guidance"]
    assert "verified candidate profile" in result["guidance"]


@pytest.mark.parametrize("job_id", ["../other", "job_fake", "job_0123456789ab/other", "", None])
def test_invalid_job_identity_cannot_touch_files(tmp_path, job_id):
    with pytest.raises(ValueError):
        context.save_source(tmp_path, job_id, {"title": "Ref", "notes": "Note"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", [
    {"title": "Empty"},
    {"title": "Bad", "url": "file:///private.txt"},
    {"title": "Bad", "url": "https://user:secret@example.org"},
    {"title": "Bad", "url": "https://example.org:bad/a"},
    {"title": "Bad", "url": "https://example.org/with space"},
    {"title": "Bad", "notes": "note", "included": "true"},
    {"title": "Bad", "text": "x" * 80001},
    {"title": "Bad", "notes": "note", "source_name": "private/file.txt", "source_sha256": "a" * 64},
])
def test_invalid_update_keeps_saved_content(tmp_path, raw):
    saved = context.save_source(tmp_path, JOB, {"title": "Good", "notes": "Original"})
    path = tmp_path / "data" / "job_context" / f"{JOB}.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        context.save_source(tmp_path, JOB, raw, source_id=saved["id"])
    assert path.read_bytes() == before


def test_snapshot_is_bounded_and_marks_truncation_without_losing_titles(tmp_path):
    for n in range(context.MAX_INCLUDED):
        context.save_source(tmp_path, JOB, {"title": f"Study {n}", "text": "x" * 20000, "notes": "n" * 6000})
    frozen = context.snapshot(tmp_path, JOB)
    assert len(frozen["sources"]) == context.MAX_INCLUDED
    assert sum(len(item["source_text"]) + len(item["user_notes"]) for item in frozen["sources"]) <= context.MAX_SNAPSHOT_CHARS
    assert all(item["notes_truncated"] and item["text_truncated"] for item in frozen["sources"])
    with pytest.raises(ValueError, match="at most"):
        context.save_source(tmp_path, JOB, {"title": "Too many", "notes": "n"})
    assert len(context.list_sources(tmp_path, JOB)) == context.MAX_INCLUDED


def test_mismatched_or_corrupt_store_not_overwritten(tmp_path):
    context.save_source(tmp_path, JOB, {"title": "A", "notes": "Original"})
    path = tmp_path / "data" / "job_context" / f"{JOB}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["job_id"] = OTHER
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="vacancy ID"):
        context.save_source(tmp_path, JOB, {"title": "B", "notes": "New"})
    assert path.read_bytes() == before
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        context.list_sources(tmp_path, JOB)


def test_file_extract_preserves_original_and_does_not_store_private_folder(tmp_path):
    original = tmp_path / "private-folder" / "study.md"
    original.parent.mkdir()
    data = b"# Study\nSample article text.\n"
    original.write_bytes(data)
    extracted = context.extract_local_text(original)
    assert extracted["source_name"] == "study.md"
    assert extracted["source_sha256"] == hashlib.sha256(data).hexdigest()
    context.save_source(tmp_path, JOB, {"title": "Study", **extracted})
    assert original.read_bytes() == data
    store_text = (tmp_path / "data" / "job_context" / f"{JOB}.json").read_text(encoding="utf-8")
    assert "private-folder" not in store_text


def test_directory_link_is_rejected_before_writing(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    try:
        (app / "data").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows session cannot create directory symlinks")
    with pytest.raises(ValueError, match="linked"):
        context.save_source(app, JOB, {"title": "Ref", "notes": "Note"})
    assert not list(target.iterdir())


def test_editor_add_and_manually_change_notes_without_ai(tmp_path, monkeypatch, tk_session_root):
    gc.collect()
    try:
        parent = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    parent.withdraw()
    try:
        dialog = JobContextDialog(parent, tmp_path, {"job_id": JOB, "company": "Example", "title": "Research role"})
        dialog.title.set("Employer article")
        dialog.url.set("https://example.org/article")
        dialog.notes.insert("1.0", "My annotation")
        assert dialog.save()
        assert context.list_sources(tmp_path, JOB)[0]["notes"] == "My annotation"
        dialog.notes.delete("1.0", "end")
        dialog.notes.insert("1.0", "Updated by hand")
        dialog.included.set(False)
        assert dialog.save()
        assert context.snapshot(tmp_path, JOB) == {}
        assert context.list_sources(tmp_path, JOB)[0]["notes"] == "Updated by hand"
        dialog.close()
    finally:
        parent.destroy()
