import gc
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pytest

from job_radar.gui_word_export import WordExportView


def descendants(widget):
    return [item for child in widget.winfo_children() for item in [child, *descendants(child)]]


@pytest.fixture
def window():
    gc.collect()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    yield root
    root.destroy()


class Harness(WordExportView):
    def __init__(self, window, tmp_path):
        self.window = window
        self.busy = False
        self.path = tmp_path
        self.calls, self.errors, self.opened = [], [], []
        self.service = SimpleNamespace(word_layouts=lambda kind: [("clean", "Clean"), ("classic", "Classic")], export_word=self.export)

    def _chosen_draft(self):
        return {"directory": self.path, "label": "Example Bio | Scientist"}

    def _error(self, exc):
        self.errors.append(str(exc))

    def open_path(self, path):
        self.opened.append(path)

    def export(self, directory, selections, date):
        self.calls.append((directory, selections, date))
        return [directory / (kind + '.docx') for kind in selections]


def test_separate_layouts_export_and_open_correct_files(window, tmp_path):
    app = Harness(window, tmp_path)
    app.open_word_export()
    window.update()
    widgets = descendants(window)
    choices = [item for item in widgets if isinstance(item, ttk.Combobox)]
    choices[1].current(1)
    buttons = {item.cget("text"): item for item in widgets if isinstance(item, ttk.Button)}
    buttons["Export Word files"].invoke()
    assert app.calls[0][1] == {"cv": "clean", "cover_letter": "classic"}
    assert not app.errors
    assert app.last_output == tmp_path
    buttons["Open Word files"].invoke()
    assert app.opened == [tmp_path / 'cv.docx', tmp_path / 'cover_letter.docx']
    # Keep the selected application fixed even when the main screen changes.
    app.path = tmp_path / 'different-job'
    buttons["Export Word files"].invoke()
    assert app.calls[-1][0] == tmp_path


def test_export_controls_visible_at_minimum_size(window, tmp_path):
    app = Harness(window, tmp_path)
    app.open_word_export()
    dialog = app._word_export_dialog
    dialog.geometry("640x610")
    window.update()
    button = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button) and widget.cget('text') == 'Export Word files')
    assert button.winfo_ismapped()
    assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()


def test_export_does_not_run_while_another_document_action_is_active(window, tmp_path):
    app = Harness(window, tmp_path)
    app.open_word_export()
    app.busy = True
    button = next(widget for widget in descendants(window) if isinstance(widget, ttk.Button) and widget.cget('text') == 'Export Word files')
    button.invoke()
    assert not app.calls
    assert 'Wait' in app.errors[0]
