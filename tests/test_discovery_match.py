import json
import socket

import pytest
import yaml

from job_radar import discovery, discovery_match as dm, job_match, providers
from job_radar.cancellation import CancellationToken, cancellation_scope


def candidate(number=1):
    preset = discovery._preset({'company': 'Example Lab', 'provider': 'greenhouse', 'board': 'example'})
    return discovery._candidate(preset, {'id': number, 'title': f'Scientist {number}',
        'location': {'name': 'Basel'}, 'absolute_url': f'https://example.org/jobs/{number}',
        'content': '<p>Research experience required. Python preferred.</p>'}, '2026-11-17T12:00:00Z')


def assessment(**changes):
    return job_match.JobMatchAssessment.model_validate({
        'category': 'plausible', 'summary': 'The documented research experience is relevant.',
        'strengths': [{'explanation': 'Research experience', 'evidence_ids': ['skills/0']}],
        'requirements': [{'requirement': 'Research experience', 'job_quote': 'Research experience required.',
                          'importance': 'required', 'assessment': 'supported',
                          'explanation': 'The profile documents research experience.', 'evidence_ids': ['skills/0']}],
        'uncertainties': [], 'next_actions': ['Check the scope of the role.'], **changes})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Unexpected network request')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    (tmp_path / 'data').mkdir()
    profile = {'contact': {'name': 'Private Name', 'email': 'private@example.org'},
               'skills': [{'id': 'r', 'text': 'Research experience'}], 'never_claim': []}
    (tmp_path / 'data/master_profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
    settings = {**providers.default_settings('ollama'), 'model': 'example:local'}
    calls = []
    def resolver(value):
        assert value['provider'] == 'ollama'
        def respond(**request):
            calls.append(request)
            return assessment(), {}
        return respond
    monkeypatch.setattr(job_match, 'resolve_response_fn', resolver)
    return tmp_path, settings, calls


def test_batch_local_only_reuses_valid_reviews_and_creates_no_tracker(setup):
    root, settings, calls = setup
    candidates = [candidate(1), candidate(2)]
    result = dm.review_candidates(root, candidates, settings)
    assert len(calls) == len(result['reviewed']) == 2
    assert not (root / 'job_radar.sqlite3').exists()
    assert not (root / 'applications').exists()
    assert all(request['use_web_search'] is False for request in calls)
    assert all('private@example.org' not in request['user'] and 'Private Name' not in request['user'] for request in calls)
    result = dm.review_candidates(root, candidates, settings)
    assert len(calls) == 2 and len(result['reused']) == 2
    reviews = dm.saved_reviews(root, candidates)
    assert all(dm.label(item) == 'Plausible' for item in reviews.values())
    assert dm.candidate_job(candidates[0])['job_id'] == candidates[0]['id'].replace('candidate_', 'job_ext_', 1)


def test_new_model_and_changed_job_do_not_reuse(setup):
    root, settings, calls = setup
    first = candidate()
    dm.review_candidates(root, [first], settings)
    dm.review_candidates(root, [first], {**settings, 'model': 'other:local'})
    assert len(calls) == 2
    first['description'] += ' New responsibility.'
    assert dm.label(dm.saved_reviews(root, [first])[first['id']]) == 'Outdated'
    dm.review_candidates(root, [first], settings)
    assert len(calls) == 3


def test_profile_change_marks_saved_review_outdated(setup):
    root, settings, calls = setup
    item = candidate()
    dm.review_candidates(root, [item], settings)
    path = root / 'data/master_profile.yaml'
    value = yaml.safe_load(path.read_text())
    value['interests'] = ['Research coordination']
    path.write_text(yaml.safe_dump(value))
    review = dm.saved_reviews(root, [item])[item['id']]
    assert dm.label(review) == 'Outdated'
    assert dm.rank(review) == dm.rank(None)
    dm.review_candidates(root, [item], settings)
    assert len(calls) == 2


@pytest.mark.parametrize('change', [{'provider': 'codex'}, {'base_url': 'https://ollama.example.org/api'}, {'model': 'example:cloud'}])
def test_other_providers_remote_endpoints_and_cloud_models_rejected(setup, change):
    root, settings, calls = setup
    with pytest.raises(ValueError):
        dm.review_candidates(root, [candidate()], {**settings, **change})
    assert not calls


def test_preflight_rejects_large_batch_stale_or_missing_descriptions_before_any_call(setup):
    root, settings, calls = setup
    with pytest.raises(ValueError, match='between 1 and 10'):
        dm.review_candidates(root, [candidate(i) for i in range(11)], settings)
    with pytest.raises(ValueError, match='refresh'):
        dm.review_candidates(root, [candidate(), {**candidate(2), 'stale': True}], settings)
    with pytest.raises(ValueError, match='description'):
        dm.review_candidates(root, [candidate(), {**candidate(2), 'description': ''}], settings)
    assert not calls


def test_stop_retains_completed_reviews_and_does_not_save_cancelled_result(setup, monkeypatch):
    root, settings, _ = setup
    token = CancellationToken()
    called = []
    def resolver(_settings):
        def respond(**request):
            called.append(request)
            if len(called) == 2:
                token.cancel()
            return assessment(), {}
        return respond
    monkeypatch.setattr(job_match, 'resolve_response_fn', resolver)
    items = [candidate(i) for i in range(3)]
    with cancellation_scope(token):
        result = dm.review_candidates(root, items, settings)
    assert result['cancelled'] and len(called) == 2
    assert result['reviewed'] == [items[0]['id']]
    assert set(dm.saved_reviews(root, items)) == {items[0]['id']}


def test_bad_model_evidence_stops_batch_without_overwriting_prior_review(setup, monkeypatch):
    root, settings, _ = setup
    items = [candidate(1), candidate(2)]
    dm.review_candidates(root, items, settings)
    before = {p: p.read_bytes() for p in (root / 'data/job_assessments').rglob('latest.json')}
    bad = assessment()
    bad.requirements[0].job_quote = 'Invented employer requirement'
    monkeypatch.setattr(job_match, 'resolve_response_fn', lambda _: lambda **kw: (bad, {}))
    result = dm.review_candidates(root, items, settings, reuse=False)
    assert len(result['errors']) == 1 and not result['reviewed']
    assert all(p.read_bytes() == value for p, value in before.items())


def test_actual_ollama_adapter_uses_native_chat_schema_and_context(setup, monkeypatch):
    root, settings, _ = setup
    requests = []
    monkeypatch.setattr(job_match, 'resolve_response_fn', providers.resolve_response_fn)
    def post(url, headers, payload, **kwargs):
        requests.append((url, payload))
        return {'done': True, 'done_reason': 'stop', 'model': settings['model'],
                'message': {'role': 'assistant', 'content': assessment().model_dump_json()}}
    monkeypatch.setattr(providers, '_post', post)
    result = dm.review_candidates(root, [candidate()], settings)
    assert not result['errors']
    url, payload = requests[0]
    assert url == 'http://localhost:11434/api/chat'
    assert payload['model'] == settings['model']
    assert payload['options']['num_ctx'] == settings['ollama_context_tokens']
    assert payload['format']['type'] == 'object'


def test_order_is_category_based_without_invented_percentages():
    assert dm.rank({'assessment': {'category': 'strong'}}) < dm.rank({'assessment': {'category': 'stretch'}})
    assert dm.rank({'assessment': {'category': 'poor'}}) < dm.rank(None)
