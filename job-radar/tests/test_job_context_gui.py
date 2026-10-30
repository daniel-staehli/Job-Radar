"""The source editor keeps edits intentional and its actions within the window."""
import gc
import tkinter as tk
from tkinter import ttk

import pytest

from job_radar import gui_job_context, job_context


JOB = "job_0123456789ab"


@pytest.fixture
def dialog(tmp_path, tk_session_root):
    gc.collect()
    try:
        parent = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    parent.geometry("1x1+0+0")
    instance = gui_job_context.JobContextDialog(parent, tmp_path, {
        "job_id": JOB, "company": "Synthetic employer", "title": "Research coordinator",
    })
    yield instance
    if parent.winfo_exists():
        parent.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.mark.parametrize("size", ["820x580", "940x710"])
def test_source_editor_actions_and_writing_fields_fit_at_supported_sizes(dialog, size):
    dialog.window.geometry(size)
    dialog.window.update()
    top = dialog.window
    left, upper = top.winfo_rootx(), top.winfo_rooty()
    right, lower = left + top.winfo_width(), upper + top.winfo_height()
    buttons = {str(widget.cget("text")): widget for widget in descendants(top) if isinstance(widget, ttk.Button)}
    for name in ("Save reference", "New reference", "Remove selected", "Done", "Load local file…"):
        button = buttons[name]
        assert button.winfo_viewable(), f"{name} is hidden at {size}"
        assert button.winfo_width() > 20 and button.winfo_height() > 10
        assert left <= button.winfo_rootx() < right
        assert upper <= button.winfo_rooty() < lower
        assert button.winfo_rootx() + button.winfo_width() <= right
        assert button.winfo_rooty() + button.winfo_height() <= lower
    assert dialog.notes.winfo_height() >= 40
    assert dialog.text.winfo_height() >= 40


def test_selecting_another_reference_can_cancel_or_save_pending_annotations(dialog, monkeypatch):
    first = job_context.save_source(dialog.root, JOB, {"title": "First paper", "notes": "First annotation"})
    second = job_context.save_source(dialog.root, JOB, {"title": "Second paper", "notes": "Second annotation"})
    dialog.reload()
    dialog.table.selection_set(first["id"])
    dialog.select()
    dialog.notes.delete("1.0", "end")
    dialog.notes.insert("1.0", "Manually revised first annotation")
    monkeypatch.setattr(gui_job_context.messagebox, "askyesnocancel", lambda *a, **kw: None)
    dialog.table.selection_set(second["id"])
    dialog.select()
    assert dialog.source_id == first["id"]
    assert dialog.table.selection() == (first["id"],)
    assert dialog.notes.get("1.0", "end-1c") == "Manually revised first annotation"
    assert job_context.list_sources(dialog.root, JOB)[0]["notes"] == "First annotation"
    monkeypatch.setattr(gui_job_context.messagebox, "askyesnocancel", lambda *a, **kw: True)
    dialog.table.selection_set(second["id"])
    dialog.select()
    assert dialog.source_id == second["id"]
    assert dialog.notes.get("1.0", "end-1c") == "Second annotation"
    assert job_context.list_sources(dialog.root, JOB)[0]["notes"] == "Manually revised first annotation"


def test_local_document_load_requires_save_and_removal_preserves_original(dialog, tmp_path, monkeypatch):
    original = tmp_path / "sample-study.txt"
    original.write_text("Synthetic paper text for this vacancy.", encoding="utf-8")
    original_bytes = original.read_bytes()
    monkeypatch.setattr(gui_job_context.filedialog, "askopenfilename", lambda *a, **kw: str(original))
    dialog.load_file()
    assert dialog.text.get("1.0", "end-1c").strip() == original_bytes.decode()
    assert job_context.list_sources(dialog.root, JOB) == []
    dialog.notes.insert("1.0", "My independent annotation.")
    assert dialog.save()
    stored = job_context.list_sources(dialog.root, JOB)[0]
    assert stored["source_name"] == original.name
    assert stored["notes"] == "My independent annotation."
    monkeypatch.setattr(gui_job_context.messagebox, "askyesno", lambda *a, **kw: True)
    dialog.remove()
    assert job_context.list_sources(dialog.root, JOB) == []
    assert original.read_bytes() == original_bytes
