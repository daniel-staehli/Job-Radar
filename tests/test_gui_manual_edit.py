"""Editor interactions use synthetic text and local callback stubs only."""
import gc
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from types import SimpleNamespace

import pytest

from job_radar.gui_manual_edit import ManualEditView


@pytest.fixture
def window(tk_session_root):
    gc.collect()
    try:
        root = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    yield root
    root.destroy()


def descendants(widget):
    result = []
    for child in widget.winfo_children():
        result.append(child)
        result.extend(descendants(child))
    return result


class Harness(ManualEditView):
    def __init__(self, window, tmp_path):
        self.window = window
        self.review_document = tk.StringVar(value="cover_letter")
        self.revision_compile_pdf = tk.BooleanVar(value=False)
        self.revision_result = tk.StringVar()
        self.status = tk.StringVar()
        self.busy = False
        self.last_output = None
        self.calls = []
        self.buttons = []
        self.errors = []
        self.output = tmp_path / "new-version"
        self.tabs = ttk.Notebook(window)
        self.review_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.review_tab, text="Review")
        self.service = SimpleNamespace(manual_document=lambda source, document: {
            "notes": "Local note", "source_fingerprint": "snapshot",
            "fields": [{"id": "subject", "label": "Subject", "value": "Original subject", "multiline": False},
                       {"id": "body", "label": "Body", "value": "Original body", "multiline": True}]},
            save_manual_document=self.save)

    def _chosen_draft(self):
        return {"directory": Path("synthetic-source")}

    def _error(self, error):
        self.errors.append(error)

    def _button(self, parent, label, action):
        button = ttk.Button(parent, text=label, command=action)
        button.pack(side="left")
        self.buttons.append(button)
        return button

    def _run(self, label, action, done, failed):
        done(action())

    def save(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"output_root": self.output, "outputs": [self.output]}

    def refresh_drafts(self, selected=None):
        self.selected = selected


def test_manual_editor_retains_section_edits_and_notes_when_saving(window, tmp_path):
    app = Harness(window, tmp_path)
    app.open_manual_editor()
    window.update()
    children = descendants(window)
    editor = next(widget for widget in children if isinstance(widget, ScrolledText)
                  and widget.get("1.0", "end-1c") == "Original subject")
    notes = next(widget for widget in children if isinstance(widget, ScrolledText)
                 and widget.get("1.0", "end-1c") == "Local note")
    choices = next(widget for widget in children if isinstance(widget, tk.Listbox))
    editor.delete("1.0", "end")
    editor.insert("1.0", "My manual subject")
    choices.selection_clear(0, "end")
    choices.selection_set(1)
    choices.event_generate("<<ListboxSelect>>")
    window.update()
    assert editor.get("1.0", "end-1c") == "Original body"
    editor.insert("end", " with an edit")
    notes.insert("end", " and a question")
    next(button for button in app.buttons if button.cget("text") == "Save changes").invoke()
    assert not app.errors
    args, kwargs = app.calls[0]
    assert args[2] == {"subject": "My manual subject", "body": "Original body with an edit"}
    assert args[3] == "Local note and a question"
    assert args[4] == "snapshot"
    assert kwargs == {"compile_pdf": False}
    assert app.selected == app.output
    assert "No model was called" in app.revision_result.get()


def test_manual_editor_save_is_visible_at_minimum_size(window, tmp_path):
    app = Harness(window, tmp_path)
    app.open_manual_editor()
    dialog = next(child for child in window.winfo_children() if isinstance(child, tk.Toplevel))
    dialog.geometry("700x560")
    window.update()
    save = next(button for button in app.buttons if button.cget("text") == "Save changes")
    assert save.winfo_ismapped()
    assert save.winfo_rooty() + save.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
