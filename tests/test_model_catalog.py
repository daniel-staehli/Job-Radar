"""Codex selector checks use a synthetic local catalog, not a live account."""
import gc
import json
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk

import pytest
from pydantic import BaseModel

from job_radar import codex_client, model_catalog, providers
from job_radar.gui_ai import CodexModelControls


class Output(BaseModel):
    answer: str


@pytest.fixture
def metadata():
    return {'models': [
        {'model': 'synthetic-large', 'efforts': ['low', 'medium', 'high', 'ultra'], 'default_effort': 'medium', 'priority': 1},
        {'model': 'synthetic-small', 'efforts': ['low', 'medium'], 'default_effort': 'low', 'priority': 2},
    ], 'configured_model': 'synthetic-large', 'configured_effort': 'high',
        'catalog_available': True, 'notes': []}


@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch):
    monkeypatch.delenv('JOB_RADAR_CODEX_MODEL', raising=False)
    monkeypatch.delenv('JOB_RADAR_CODEX_REASONING', raising=False)


def test_read_projects_only_model_metadata_and_filters_hidden_records(tmp_path):
    (tmp_path / 'models_cache.json').write_text(json.dumps({'secret': 'do-not-return', 'models': [
        {'slug': 'synthetic-large', 'priority': 2, 'default_reasoning_level': 'medium',
         'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'medium'}, {'effort': 'made-up'}], 'instructions': 'do-not-return'},
        {'slug': 'hidden', 'visibility': 'hide'}, {'slug': 'bad model'}, {'slug': 'broken', 'supported_reasoning_levels': None},
    ]}), encoding='utf-8')
    (tmp_path / 'config.toml').write_text('model = "synthetic-large"\nmodel_reasoning_effort = "high"\napi_key = "do-not-return"\n', encoding='utf-8')
    result = model_catalog.read_catalog(tmp_path)
    assert result['models'] == [{'model': 'synthetic-large', 'priority': 2, 'default_effort': 'medium', 'efforts': ['low', 'medium']}]
    assert result['configured_model'] == 'synthetic-large' and result['configured_effort'] == 'high'
    assert 'do-not-return' not in str(result)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['config.toml', 'models_cache.json']


def test_legacy_named_profile_model_defaults_are_resolved(tmp_path):
    (tmp_path / 'config.toml').write_text('model = "base"\nprofile = "writing"\n[profiles.writing]\nmodel = "selected"\nmodel_reasoning_effort = "low"\n', encoding='utf-8')
    result = model_catalog.read_catalog(tmp_path)
    assert result['configured_model'] == 'selected' and result['configured_effort'] == 'low'
    assert not result['catalog_available']


def test_missing_corrupt_and_oversized_catalogs_do_not_break_settings(tmp_path):
    assert model_catalog.read_catalog(tmp_path)['models'] == []
    (tmp_path / 'models_cache.json').write_text('{broken', encoding='utf-8')
    assert not model_catalog.read_catalog(tmp_path)['catalog_available']
    (tmp_path / 'models_cache.json').write_bytes(b' ' * (4 * 1024 * 1024 + 1))
    assert not model_catalog.read_catalog(tmp_path)['catalog_available']


def test_unknown_model_and_incompatible_effort_fail_without_fallback(metadata):
    with pytest.raises(ValueError, match='not in Codex'):
        model_catalog.resolve_selection({'model': 'synthetic-lagre'}, catalog=metadata)
    with pytest.raises(ValueError, match='does not list'):
        model_catalog.resolve_selection({'model': 'synthetic-small', 'reasoning_effort': 'ultra'}, catalog=metadata)
    custom = model_catalog.resolve_selection({'model': 'new-exact-id', 'custom_model': True, 'reasoning_effort': 'high'}, catalog=metadata)
    assert custom['model'] == 'new-exact-id' and not custom['model_in_local_catalog']


def test_explicit_choices_override_environment_and_model_default_is_visible(metadata, monkeypatch):
    monkeypatch.setenv('JOB_RADAR_CODEX_MODEL', 'wrong-environment-model')
    monkeypatch.setenv('JOB_RADAR_CODEX_REASONING', 'ultra')
    chosen = model_catalog.resolve_selection({'model': 'synthetic-small', 'reasoning_effort': 'medium'}, catalog=metadata)
    assert chosen['model'] == 'synthetic-small' and chosen['reasoning_effort'] == 'medium'
    assert model_catalog.resolve_selection({'model': 'synthetic-small'}, catalog=metadata)['reasoning_effort'] == 'low'


def test_default_choice_resolves_real_config_values(metadata):
    selected = model_catalog.resolve_selection({}, catalog=metadata)
    assert selected['model'] == 'synthetic-large' and selected['reasoning_effort'] == 'high'
    assert selected['model_source'] == 'Codex config.toml'


def test_unknown_default_never_reaches_model_but_custom_fallback_is_available(tmp_path, monkeypatch):
    catalog = model_catalog.read_catalog(tmp_path)
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: catalog)
    monkeypatch.setattr(codex_client, 'structured_response', lambda **kw: pytest.fail('No request allowed'))
    value = providers.default_settings()
    assert 'not known locally' in providers.describe(value)
    assert not providers.settings_check(value)['ready']
    with pytest.raises(ValueError, match='default model is not known'):
        providers.resolve_response_fn(value)(schema_type=Output, schema_name='x', system='s', user='u', use_web_search=False)
    custom = {**value, 'model': 'future-exact-model', 'custom_model': True, 'reasoning_effort': 'high'}
    assert providers.normalize_settings(custom, require_model=True)['model'] == 'future-exact-model'


def test_old_free_text_settings_remain_explicit_custom_without_rewriting(tmp_path, metadata, monkeypatch):
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: metadata)
    path = tmp_path / 'data' / 'ai_settings.json'
    path.parent.mkdir()
    text = json.dumps({'schema_version': 1, 'provider': 'codex', 'model': 'old-model', 'base_url': '', 'api_key_env': ''})
    path.write_text(text, encoding='utf-8')
    settings = providers.load_settings(tmp_path)
    assert settings['model'] == 'old-model' and settings['custom_model'] is True
    assert path.read_text(encoding='utf-8') == text


def test_provider_passes_identical_explicit_model_and_effort_to_preview_audit_and_client(metadata, monkeypatch):
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: metadata)
    monkeypatch.setenv('JOB_RADAR_CODEX_MODEL', 'unwanted-model')
    monkeypatch.setenv('JOB_RADAR_CODEX_REASONING', 'ultra')
    settings = {**providers.default_settings(), 'model': 'synthetic-small', 'reasoning_effort': 'medium'}
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return Output(answer='ok'), {}
    monkeypatch.setattr(codex_client, 'structured_response', fake)
    before = providers.provider_identity(settings)
    preview = json.loads(providers.request_preview(settings, 's', 'u', Output))
    _, raw = providers.resolve_response_fn(settings)(schema_type=Output, schema_name='x', system='s', user='u', use_web_search=False, reasoning_effort='high')
    assert before['model'] == preview['model'] == calls[0]['model'] == 'synthetic-small'
    assert before['reasoning_effort'] == preview['reasoning_effort'] == calls[0]['reasoning_effort'] == 'medium'
    assert calls[0]['use_environment'] is False and raw['provider_settings'] == before
    assert providers.provider_identity({**settings, 'reasoning_effort': 'low'}) != before


def test_explicit_client_arguments_cannot_be_overridden_by_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_client, 'codex_executable', lambda: 'synthetic-codex')
    monkeypatch.setenv('JOB_RADAR_CODEX_MODEL', 'unwanted-model')
    monkeypatch.setenv('JOB_RADAR_CODEX_REASONING', 'ultra')
    commands = []
    def fake(command, **kwargs):
        commands.append(command)
        Path(command[command.index('--output-last-message') + 1]).write_text('{"answer":"ok"}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(codex_client.subprocess, 'run', fake)
    _, raw = codex_client.structured_response(schema_type=Output, schema_name='x', system='s', user='u',
                                             use_web_search=False, model='synthetic-small', reasoning_effort='low',
                                             use_environment=False, artifact_dir=tmp_path)
    command = commands[0]
    assert command[command.index('--model') + 1] == 'synthetic-small'
    assert 'model_reasoning_effort="low"' in command
    assert 'unwanted-model' not in command and 'model_reasoning_effort="ultra"' not in command
    assert raw['model_override'] == 'synthetic-small' and raw['reasoning_effort'] == 'low'


def test_default_provider_passes_resolved_config_even_with_ignore_user_config(tmp_path, metadata, monkeypatch):
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: metadata)
    monkeypatch.setattr(codex_client, 'codex_executable', lambda: 'synthetic-codex')
    commands = []
    def fake(command, **kwargs):
        commands.append(command)
        Path(command[command.index('--output-last-message') + 1]).write_text('{"answer":"ok"}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(codex_client.subprocess, 'run', fake)
    _, raw = providers.resolve_response_fn(providers.default_settings())(
        schema_type=Output, schema_name='x', system='s', user='u', use_web_search=False, artifact_dir=tmp_path)
    command = commands[0]
    assert '--ignore-user-config' in command
    assert command[command.index('--model') + 1] == 'synthetic-large'
    assert 'model_reasoning_effort="high"' in command
    assert raw['provider_settings']['model'] == raw['model_override'] == 'synthetic-large'
    assert raw['provider_settings']['reasoning_effort'] == raw['reasoning_effort'] == 'high'


def test_settings_reject_typo_before_saving_or_running(tmp_path, metadata, monkeypatch):
    monkeypatch.setattr(model_catalog, 'read_catalog', lambda: metadata)
    settings = {**providers.default_settings(), 'model': 'synthetic-lagre'}
    with pytest.raises(ValueError, match='not in Codex'):
        providers.save_settings(tmp_path, settings)
    assert not (tmp_path / 'data').exists()
    assert not providers.settings_check(settings)['ready']


def test_gui_picker_is_readonly_and_changes_supported_efforts(metadata, tk_session_root):
    gc.collect()
    try:
        window = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f'Tk unavailable: {exc}')
    window.withdraw()
    try:
        controls = CodexModelControls(window, {'model': 'synthetic-large', 'reasoning_effort': 'ultra'}, catalog=metadata)
        assert str(controls.model_choice['state']) == str(controls.effort_choice['state']) == 'readonly'
        assert controls.values()['reasoning_effort'] == 'ultra'
        controls.model.set('synthetic-small')
        assert 'ultra' not in controls.effort_choice['values']
        assert controls.values()['reasoning_effort'] == ''
        assert 'reasoning: low' in controls.detail.get()
        controls.model.set(controls.CUSTOM)
        controls.custom.set('new-model')
        controls.effort.set('high')
        assert controls.values() == {'model': 'new-model', 'reasoning_effort': 'high', 'custom_model': True}
        assert 'not verified' in controls.detail.get()
    finally:
        window.destroy()
