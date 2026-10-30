"""Ollama context selection stays local and independent of thinking controls."""
import tkinter as tk
from tkinter import ttk

import pytest

from job_radar import gui_ai, providers, model_catalog
from job_radar.gui_ai import OllamaContextControls, OllamaThinkingControls
from job_radar.gui import JobRadarApp
from job_radar.gui_services import GuiService
from test_gui_ollama import tk_root, window


def descendants(parent):
    for widget in parent.winfo_children():
        yield widget
        yield from descendants(widget)


def dialog_widgets(window):
    dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
    return dialog, list(descendants(dialog))


def button(widgets, label):
    return next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == label)


def test_ollama_context_picker_upgrades_missing_setting_and_is_readonly(window):
    picker = OllamaContextControls(window, {})
    assert picker.value() == providers.OLLAMA_CONTEXT_DEFAULT == 32768
    assert str(picker.selector['state']) == 'readonly'
    assert set(picker.options.values()) == set(providers.OLLAMA_CONTEXT_CHOICES)
    assert 'tokens' in picker.choice.get()
    picker.choice.set('wrong')
    with pytest.raises(ValueError, match='context window from the list'):
        picker.value()


@pytest.mark.parametrize('capacity', [8192, 65536, 262144])
def test_ollama_context_picker_restores_saved_capacity(window, capacity):
    picker = OllamaContextControls(window, {'ollama_context_tokens': capacity})
    assert picker.value() == capacity
    picker.choice.set('32,768 tokens')
    assert picker.value() == 32768


@pytest.fixture
def app(window, tmp_path, monkeypatch):
    catalog = {'models': [], 'configured_model': 'synthetic-default',
               'configured_effort': 'medium', 'catalog_available': False, 'notes': []}
    monkeypatch.setattr(gui_ai, 'read_catalog', lambda: catalog)
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: catalog)
    monkeypatch.delenv('JOB_RADAR_CODEX_MODEL', raising=False)
    monkeypatch.delenv('JOB_RADAR_CODEX_REASONING', raising=False)
    monkeypatch.setattr(gui_ai, 'ollama_thinking_options', lambda _: pytest.fail('Opening/saving context must not contact Ollama'))
    service = GuiService(tmp_path)
    original = {**providers.default_settings('ollama'), 'model': 'fictional-model',
                'ollama_thinking': 'high', 'ollama_context_tokens': 16384}
    service.save_ai_settings(original)
    return JobRadarApp(window, service)


def test_ollama_context_dialog_saves_capacity_without_changing_model_or_thinking(app, window):
    app.open_ai_settings()
    dialog, widgets = dialog_widgets(window)
    context = next(w for w in widgets if isinstance(w, OllamaContextControls))
    thinking = next(w for w in widgets if isinstance(w, OllamaThinkingControls))
    assert context.value() == 16384 and thinking.value() == 'high'
    context.choice.set('65,536 tokens')
    button(widgets, 'Save settings').invoke()
    saved = app.service.ai_settings()
    assert saved['provider'] == 'ollama'
    assert saved['model'] == 'fictional-model'
    assert saved['ollama_thinking'] == 'high'
    assert saved['ollama_context_tokens'] == 65536
    app.open_ai_settings()
    _, widgets = dialog_widgets(window)
    assert next(w for w in widgets if isinstance(w, OllamaContextControls)).value() == 65536


def test_ollama_context_hidden_for_other_provider_and_not_sent_when_saving(app, window, monkeypatch):
    captured=[]
    save=app.service.save_ai_settings
    def record(settings):
        captured.append(dict(settings))
        return save(settings)
    monkeypatch.setattr(app.service,'save_ai_settings',record)
    app.open_ai_settings()
    _, widgets = dialog_widgets(window)
    context = next(w for w in widgets if isinstance(w, OllamaContextControls))
    provider = next(w for w in widgets if isinstance(w, ttk.Combobox) and 'Ollama (local)' in w['values'])
    provider.set('Codex')
    assert not context.grid_info()
    button(widgets, 'Save settings').invoke()
    assert captured[-1]['provider']=='codex'
    assert 'ollama_context_tokens' not in captured[-1]
    assert 'ollama_thinking' not in captured[-1]


def test_ollama_context_cancel_leaves_saved_setting_unchanged(app, window):
    original=app.service.ai_settings()
    app.open_ai_settings()
    _, widgets=dialog_widgets(window)
    next(w for w in widgets if isinstance(w,OllamaContextControls)).choice.set('131,072 tokens')
    button(widgets,'Cancel').invoke()
    assert app.service.ai_settings()==original


def test_ollama_context_invalid_programmatic_choice_does_not_save(app, window):
    original=app.service.ai_settings()
    app.open_ai_settings()
    dialog,widgets=dialog_widgets(window)
    next(w for w in widgets if isinstance(w,OllamaContextControls)).choice.set('unlisted value')
    button(widgets,'Save settings').invoke()
    assert dialog.winfo_exists()
    assert app.service.ai_settings()==original


def test_ollama_context_minimum_dialog_keeps_action_buttons_visible(app, window):
    app.open_ai_settings()
    dialog, widgets=dialog_widgets(window)
    dialog.geometry('760x580')
    dialog.update_idletasks()
    save=button(widgets,'Save settings')
    assert save.winfo_rooty()+save.winfo_height() <= dialog.winfo_rooty()+dialog.winfo_height()
    canvases=[w for w in widgets if isinstance(w,tk.Canvas)]
    assert len(canvases)==1
    # Added controls may extend the content; its container remains scrollable.
    assert canvases[0].cget('yscrollcommand')
