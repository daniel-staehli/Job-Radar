"""Ollama picker tests use fake metadata; no model or network is contacted."""
import gc
import threading
import time
import tkinter as tk

import pytest

from job_radar import gui_ai, providers
from job_radar.gui_ai import OllamaThinkingControls


@pytest.fixture(scope="module")
def tk_root(tk_session_root):
    gc.collect()
    try:
        root = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f'Tk unavailable: {exc}')
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root):
    yield tk_root
    for timer in tk_root.tk.call('after', 'info'):
        tk_root.after_cancel(timer)
    for child in tk_root.winfo_children():
        child.destroy()
    gc.collect()


def controls(window, current=None):
    settings = {**providers.default_settings('ollama'), 'model': 'fictional-model'}
    if current:
        settings.update(current)
    return OllamaThinkingControls(window, lambda: dict(settings), settings), settings


def finish(window, picker):
    deadline = time.monotonic() + 4
    while picker._busy and time.monotonic() < deadline:
        window.update()
        time.sleep(0.01)
    assert not picker._busy, 'Metadata worker did not finish'


@pytest.mark.parametrize('values,choice,expected', [
    ([True, False], 'On', True),
    ([True, False], 'Off', False),
    (['low', 'medium', 'high'], 'Level: high', 'high'),
])
def test_picker_offers_exact_server_controls(window, monkeypatch, values, choice, expected):
    picker, settings = controls(window)
    requests = []
    def metadata(value):
        requests.append(value)
        return {'values': values, 'default': values[0], 'detail': 'Supported controls.'}
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', metadata)
    assert picker.value() is None
    picker.load_options()
    finish(window, picker)
    assert str(picker.selector['state']) == 'readonly'
    picker.choice.set(choice)
    assert type(picker.value()) is type(expected) and picker.value() == expected
    assert requests == [providers.normalize_settings(settings, require_model=True)]
    assert 'Server default:' in picker.detail.get()
    picker.choice.set('ultra')
    with pytest.raises(ValueError, match='from the list'):
        picker.value()


@pytest.mark.parametrize('values', [[], [False]])
def test_missing_or_non_thinking_model_does_not_invent_levels(window, monkeypatch, values):
    picker, _ = controls(window)
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', lambda _: {
        'values': values, 'default': None, 'detail': 'No named thinking levels.'})
    picker.load_options()
    finish(window, picker)
    assert picker.value() is None
    assert list(picker.options) == ['Model default'] + (['Off'] if values else [])


def test_reopened_saved_choice_is_preserved_without_connection_and_rechecked(window, monkeypatch):
    picker, _ = controls(window, {'ollama_thinking': 'high'})
    assert picker.value() == 'high'
    assert 'not checked' in picker.choice.get()
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', lambda _: {
        'values': ['low'], 'default': 'low', 'detail': 'Only low is supported.'})
    picker.load_options()
    finish(window, picker)
    assert picker.value() is None
    assert 'not supported' in picker.detail.get()


def test_failed_probe_keeps_saved_selection_and_can_be_retried(window, monkeypatch):
    picker, _ = controls(window, {'ollama_thinking': False})
    def failed(_):
        raise providers.ProviderError('Local server unavailable.')
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', failed)
    picker.load_options()
    finish(window, picker)
    assert picker.value() is False
    assert 'Local server unavailable' in picker.detail.get()
    assert str(picker.load_button['state']) == 'normal'


def test_changing_model_discards_stale_reply_and_resets_saved_override(window, monkeypatch):
    picker, settings = controls(window, {'ollama_thinking': 'high'})
    gate = threading.Event()
    def metadata(_):
        gate.wait(2)
        return {'values': ['high'], 'default': 'high', 'detail': 'Old model result.'}
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', metadata)
    picker.load_options()
    settings['model'] = 'different-model'
    picker.inputs_changed()
    assert picker.value() is None
    gate.set()
    finish(window, picker)
    assert list(picker.options) == ['Model default']
    assert 'Old model' not in picker.detail.get()


def test_closing_picker_cancels_ui_poll_without_worker_using_tk(window, monkeypatch):
    picker, _ = controls(window)
    gate = threading.Event()
    def metadata(_):
        gate.wait(2)
        return {'values': [True], 'default': True, 'detail': 'Ready.'}
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', metadata)
    picker.load_options()
    picker.destroy()
    assert picker._poll_id is None
    gate.set()
    window.update()


def test_empty_model_does_not_contact_server(window, monkeypatch):
    picker, settings = controls(window)
    settings['model'] = ''
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', lambda _: pytest.fail('No request expected'))
    picker.load_options()
    assert not picker._busy
    assert 'model' in picker.detail.get().lower()


def test_settings_dialog_saves_ollama_thinking_and_can_return_to_codex(window, tmp_path, monkeypatch):
    from tkinter import ttk
    from job_radar.gui import JobRadarApp
    from job_radar.gui_services import GuiService
    from job_radar import model_catalog
    metadata = {'models': [], 'configured_model': 'synthetic-default',
                'configured_effort': 'medium', 'catalog_available': False, 'notes': []}
    monkeypatch.setattr(gui_ai, 'read_catalog', lambda: metadata)
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: metadata)
    monkeypatch.delenv('JOB_RADAR_CODEX_MODEL', raising=False)
    monkeypatch.delenv('JOB_RADAR_CODEX_REASONING', raising=False)
    service = GuiService(tmp_path)
    original = {**providers.default_settings('ollama'), 'model': 'fictional-model', 'ollama_thinking': 'high'}
    service.save_ai_settings(original)
    app = JobRadarApp(window, service)
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', lambda _: pytest.fail('Saving must not probe the server'))
    app.open_ai_settings()
    dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
    def descendants(parent):
        for widget in parent.winfo_children():
            yield widget
            yield from descendants(widget)
    widgets = list(descendants(dialog))
    picker = next(w for w in widgets if isinstance(w, OllamaThinkingControls))
    assert picker.value() == 'high'
    save = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == 'Save settings')
    save.invoke()
    assert service.ai_settings() == original
    app.open_ai_settings()
    dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
    widgets = list(descendants(dialog))
    provider = next(w for w in widgets if isinstance(w, ttk.Combobox) and 'Ollama (local)' in w['values'])
    provider.set('Codex')
    next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == 'Save settings').invoke()
    saved = service.ai_settings()
    assert saved['provider'] == 'codex'
    assert saved['ollama_thinking'] is None
    assert saved['reasoning_effort'] == ''  # Existing configured default remains authoritative.
