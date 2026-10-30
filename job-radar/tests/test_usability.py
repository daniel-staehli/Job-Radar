"""User actions save the intended data locally and explain empty discovery."""
from job_radar.discovery import suggested_presets
from test_gui import app, finish, save_through_form


def test_status_selection_saves_immediately_and_preserves_other_unsaved_fields(app):
    identifier = save_through_form(app)
    app.notes.insert('1.0', 'A private unsaved note')
    app.tracking['next_action'].set('Write to the contact')
    app.tracking['status'].set('applied')
    app.status_choice.event_generate('<<ComboboxSelected>>')
    app.window.update()
    saved = app.service.jobs()[0]
    assert saved['status'] == 'applied'
    assert saved['application_date']
    assert app.tracking['application_date'].get() == saved['application_date']
    assert app.job_tree.item(identifier, 'values')[2] == 'applied'
    assert app.metrics['active'].get() == '1'
    assert saved['notes'] is None and saved['next_action'] is None
    assert app.notes.get('1.0', 'end-1c') == 'A private unsaved note'
    assert app.tracking['next_action'].get() == 'Write to the contact'
    assert 'Status saved' in app.status.get()
    assert 'Saving after typing' in app.tracking_notice.get()
    app.save_tracking()
    finish(app)
    saved = app.service.jobs()[0]
    assert saved['notes'] == 'A private unsaved note'
    assert saved['next_action'] == 'Write to the contact'
    assert not app.test_errors


def test_status_failure_reverts_the_control_and_keeps_saved_value(app, monkeypatch):
    identifier = save_through_form(app)
    def fail(*_):
        raise OSError('Synthetic write failure')
    monkeypatch.setattr(app.service, 'update_tracking', fail)
    app.tracking['status'].set('rejected')
    app.status_choice.event_generate('<<ComboboxSelected>>')
    assert app.tracking['status'].get() == 'interested'
    assert app.service.jobs()[0]['status'] == 'interested'
    assert app.job_tree.item(identifier, 'values')[2] == 'interested'
    assert app.test_errors == ['Synthetic write failure']


def test_saved_status_stays_visible_when_it_leaves_active_filter(app):
    identifier = save_through_form(app)
    app.status_filter.set('interested')
    app.window.update()
    app.notes.insert('1.0', 'Keep my draft note')
    app.tracking['status'].set('interview')
    app.status_choice.event_generate('<<ComboboxSelected>>')
    app.window.update()
    assert app.status_filter.get() == 'All statuses'
    assert app.job_tree.selection() == (identifier,)
    assert app.job_tree.item(identifier, 'values')[2] == 'interview'
    assert app.notes.get('1.0', 'end-1c') == 'Keep my draft note'
    assert not app.test_errors


def test_empty_discovery_explains_setup_and_refresh_opens_employers(app, monkeypatch):
    assert 'No employers added' in app.discovery_empty.cget('text')
    assert 'No employers added' in app.discovery_status.get()
    calls = []
    monkeypatch.setattr(app, 'manage_employers', lambda: calls.append('setup'))
    app.refresh_discovery()
    assert calls == ['setup']
    assert not app.service.db_path.exists()
    assert not app.test_errors


def test_employer_suggestions_are_deduplicated_and_reject_unrelated_urls():
    jobs = [
        {'company': 'Example Bio', 'source_url': 'https://jobs.ashbyhq.com/example/a'},
        {'company': 'Example Bio', 'apply_url': 'https://jobs.ashbyhq.com/example/b'},
        {'company': 'Other Lab', 'source_url': 'https://job-boards.greenhouse.io/other/jobs/321'},
        {'company': 'Unsupported', 'source_url': 'https://careers.example.org/jobs'},
        {'company': 'Lookalike', 'source_url': 'https://jobs.ashbyhq.com.evil.invalid/example'},
        {'company': 'Credentials', 'source_url': 'https://secret@jobs.ashbyhq.com/private'},
    ]
    assert [(p['company'], p['provider'], p['board']) for p in suggested_presets(jobs)] == [
        ('Example Bio', 'ashby', 'example'), ('Other Lab', 'greenhouse', 'other')]


def test_employer_picker_filters_cached_results(app):
    for company, board in [('First', 'first'), ('Second', 'second')]:
        app.service.save_employer({'company': company, 'provider': 'ashby', 'board': board})
    app._load_discovery()
    app.candidates = [
        {'id': f'result-{i}', 'preset_id': preset['id'], 'company': preset['company'],
         'title': 'Research role', 'location': 'Lausanne', 'description': 'Research',
         'provider': 'ashby', 'source_url': f"https://jobs.ashbyhq.com/{preset['board']}/role"}
        for i, preset in enumerate(app.presets)
    ]
    app.employer_choice.current(2)
    app.employer_choice.event_generate('<<ComboboxSelected>>')
    assert app.candidate_tree.get_children() == ('result-1',)
    app.employer_choice.current(0)
    app.employer_choice.event_generate('<<ComboboxSelected>>')
    assert len(app.candidate_tree.get_children()) == 2
    assert not app.test_errors
