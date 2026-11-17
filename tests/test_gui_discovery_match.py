import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import gc

import pytest
import yaml

from job_radar import discovery_match as dm, providers
from job_radar.gui_discovery_match import DiscoveryMatchView
from test_discovery_match import candidate


def descendants(widget):
    return [item for child in widget.winfo_children() for item in [child, *descendants(child)]]


@pytest.fixture
def app(tmp_path):
    gc.collect()
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tk unavailable: {exc}')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/master_profile.yaml').write_text(yaml.safe_dump({'skills': ['Research']}))
    instance = Harness(window, tmp_path)
    yield instance
    for timer in window.tk.call('after', 'info'):
        window.after_cancel(timer)
    window.destroy()


class Harness(DiscoveryMatchView):
    def __init__(self, window, root):
        self.window = window
        self.service = SimpleNamespace(root=root)
        self.busy = False
        self.errors, self.calls = [], []
        self.settings = {**providers.default_settings('ollama'), 'model': 'example:local'}
        self.candidates = [candidate(i) for i in range(12)]
        self.candidate_tree = ttk.Treeview(window)
        for item in self.candidates:
            self.candidate_tree.insert('', 'end', iid=item['id'])
        self.candidate_tree.selection_set(self.candidates[1]['id'])
        self.discovery_ai_sort = tk.BooleanVar(value=False)
        self.discovery_status = tk.StringVar()
        self.status = tk.StringVar()
        self.dirty = False

    def _profile_has_unsaved_changes(self):
        return self.dirty

    def _selected_ai(self):
        return self.settings

    def _error(self, exc):
        self.errors.append(str(exc))

    def _fill_candidates(self):
        pass

    def _run(self, label, action, done, failed):
        self.calls.append((action, done, failed))


def button(app, label):
    return next(widget for widget in descendants(app._discovery_review_dialog)
                if isinstance(widget, ttk.Button) and widget.cget('text') == label)


def test_selection_limit_provider_and_stop(app, monkeypatch):
    app.open_discovery_review()
    app.window.update()
    tree = next(widget for widget in descendants(app._discovery_review_dialog) if isinstance(widget, ttk.Treeview))
    assert tree.selection() == (app.candidates[1]['id'],)
    tree.selection_set(tree.get_children())
    button(app, 'Review selected vacancies').invoke()
    assert not app.calls
    tree.selection_set(app.candidates[1]['id'])
    button(app, 'Review selected vacancies').invoke()
    assert len(app.calls) == 1
    button(app, 'Stop review').invoke()
    action, done, failed = app.calls[0]
    with pytest.raises(Exception, match='aborted'):
        action()
    failed(RuntimeError('Stopped'))
    assert app.discovery_ai_sort.get()
    assert 'remain saved' in app.discovery_status.get()


def test_dialog_snapshots_selected_jobs_and_model_and_refreshes_after_success(app, monkeypatch):
    app.open_discovery_review()
    button(app, 'Review selected vacancies').invoke()
    app.settings = {**app.settings, 'model': 'changed-later'}
    app.candidates = []
    seen = []
    def review(root, jobs, settings, **kwargs):
        seen.append((jobs, settings))
        return {'reviewed': [jobs[0]['id']], 'reused': [], 'errors': [], 'cancelled': False}
    monkeypatch.setattr(dm, 'review_candidates', review)
    action, done, _ = app.calls[0]
    done(action())
    assert seen[0][0][0]['title'] == 'Scientist 1'
    assert seen[0][1]['model'] == 'example:local'
    assert '1 new reviews' in app.discovery_status.get()
    assert app.discovery_ai_sort.get()


def test_non_ollama_and_unsaved_profile_are_rejected_before_opening(app):
    app.settings = providers.default_settings('codex')
    app.open_discovery_review()
    assert app.errors and not app.calls
    app.settings = {**providers.default_settings('ollama'), 'model': 'example:local'}
    app.dirty = True
    app.open_discovery_review()
    assert 'Save your changes' in app.errors[-1]
