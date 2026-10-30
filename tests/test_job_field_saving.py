"""Tracking edits survive ordinary navigation and failed local saves."""
import sqlite3
import threading
import time

import pytest

from test_gui import app, finish, save_through_form
from job_radar.gui_services import GuiService
from test_cv import job as synthetic_job


def pump(app, seconds=0.85):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.window.update()
        time.sleep(0.01)
    app.window.update()


def saved_job(app, identifier):
    return next(job for job in app.service.jobs() if job['job_id'] == identifier)


def wait_for_saved(app, identifier, **expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.window.update()
        saved = saved_job(app, identifier)
        if all(saved.get(key) == value for key, value in expected.items()):
            return saved
        time.sleep(0.01)
    assert {key: saved.get(key) for key in expected} == expected


def two_jobs(app):
    first = save_through_form(app, company='First Example')
    second = save_through_form(app, company='Second Example',
                               source_url='https://example.org/jobs/two')
    app.job_tree.selection_set(first)
    app.window.update()
    assert app.selected_job['job_id'] == first
    return first, second


def try_navigation(app, action, second, monkeypatch):
    if action == 'switch':
        app.job_tree.selection_set(second)
        app.window.update()
    elif action == 'refresh':
        app.refresh()
        app.window.update()
    elif action == 'filter':
        app.search.set('Second Example')
        app.window.update()
    else:
        closed = []
        with monkeypatch.context() as patch:
            patch.setattr(app.window, 'destroy', lambda: closed.append(True))
            app._close()
        assert not closed, 'Closing must not discard an edit which could not be saved'


def test_all_tracking_fields_and_notes_save_without_clicking_save(app):
    identifier = save_through_form(app)
    values = {
        'application_date': '2026-09-20',
        'follow_up_date': '2026-10-20',
        'next_action': 'Ask about the research team',
        'contact': 'Example recruiter',
        'cv_version': 'research-cv-v2.tex',
        'cover_letter': 'research-letter-v1.tex',
        'interview_stage': 'Technical conversation',
        'interview_dates': '2026-10-15, 2026-10-19',
    }
    for key, value in values.items():
        app.tracking[key].set(value)
    app.notes.insert('1.0', 'Bring the project outline.\nDiscuss working hours.')
    expected = {**values, 'interview_dates': ['2026-10-15', '2026-10-19'],
                'notes': 'Bring the project outline.\nDiscuss working hours.'}
    wait_for_saved(app, identifier, **expected)
    app.refresh()
    app.window.update()
    assert app.tracking['contact'].get() == values['contact']
    assert app.notes.get('1.0', 'end-1c') == expected['notes']
    assert not app.test_errors
    # A newly opened service reads the committed fields, not a GUI cache.
    reopened = next(row for row in GuiService(app.service.root).jobs() if row['job_id'] == identifier)
    assert all(reopened.get(key) == value for key, value in expected.items())


def test_clearing_text_dates_and_notes_is_also_saved(app):
    identifier = save_through_form(app)
    app.tracking['contact'].set('Example contact')
    app.tracking['follow_up_date'].set('2026-10-21')
    app.tracking['interview_dates'].set('2026-10-18')
    app.notes.insert('1.0', 'An old note')
    app.save_tracking()
    finish(app)
    for key in ('contact', 'follow_up_date', 'interview_dates'):
        app.tracking[key].set('')
    app.notes.delete('1.0', 'end')
    wait_for_saved(app, identifier, contact=None, follow_up_date=None,
                   interview_dates=[], notes=None)


def test_immediate_row_change_saves_edits_to_the_original_job(app):
    first, second = two_jobs(app)
    app.tracking['next_action'].set('Follow up with the first employer')
    app.notes.insert('1.0', 'Notes belonging only to the first job')
    app.job_tree.selection_set(second)
    app.window.update()
    assert app.selected_job['job_id'] == second
    assert saved_job(app, first)['next_action'] == 'Follow up with the first employer'
    assert saved_job(app, first)['notes'] == 'Notes belonging only to the first job'
    assert saved_job(app, second)['next_action'] is None
    assert saved_job(app, second)['notes'] is None
    assert app.notes.get('1.0', 'end-1c') == ''


@pytest.mark.parametrize('action', ['refresh', 'filter'])
def test_refresh_and_filters_flush_edits_before_rebuilding_the_list(app, action):
    first, _ = two_jobs(app)
    app.tracking['contact'].set('First employer contact')
    app.notes.insert('1.0', 'Keep this even when this row disappears')
    if action == 'refresh':
        app.refresh()
    else:
        app.search.set('Second Example')
    app.window.update()
    assert saved_job(app, first)['contact'] == 'First employer contact'
    assert saved_job(app, first)['notes'] == 'Keep this even when this row disappears'
    assert not app.test_errors


def test_status_change_and_filter_rebuild_keep_pending_text(app):
    identifier = save_through_form(app)
    app.status_filter.set('interested')
    app.window.update()
    app.tracking['contact'].set('Research coordinator')
    app.notes.insert('1.0', 'A note typed just before changing status')
    app.tracking['status'].set('applied')
    app.status_choice.event_generate('<<ComboboxSelected>>')
    app.window.update()
    assert saved_job(app, identifier)['status'] == 'applied'
    assert app.tracking['contact'].get() == 'Research coordinator'
    assert app.notes.get('1.0', 'end-1c') == 'A note typed just before changing status'
    wait_for_saved(app, identifier, contact='Research coordinator',
                   notes='A note typed just before changing status')
    assert saved_job(app, identifier)['application_date']
    assert not app.test_errors


def test_close_saves_pending_edits_before_destroying_the_window(app, monkeypatch):
    identifier = save_through_form(app)
    app.tracking['next_action'].set('Prepare interview questions')
    app.notes.insert('1.0', 'Entered immediately before closing')
    at_close = []
    with monkeypatch.context() as patch:
        patch.setattr(app.window, 'destroy', lambda: at_close.append(saved_job(app, identifier)))
        app._close()
    assert len(at_close) == 1
    assert at_close[0]['next_action'] == 'Prepare interview questions'
    assert at_close[0]['notes'] == 'Entered immediately before closing'


@pytest.mark.parametrize('action', ['switch', 'refresh', 'filter', 'close'])
def test_incomplete_date_prevents_navigation_discarding_the_form(app, monkeypatch, action):
    first, second = two_jobs(app)
    app.tracking['follow_up_date'].set('2026-10-')
    app.tracking['contact'].set('Keep this unfinished draft')
    app.notes.insert('1.0', 'Also keep these notes')
    try_navigation(app, action, second, monkeypatch)
    assert app.selected_job['job_id'] == first
    assert app.job_tree.selection() == (first,)
    assert app.tracking['follow_up_date'].get() == '2026-10-'
    assert app.tracking['contact'].get() == 'Keep this unfinished draft'
    assert app.notes.get('1.0', 'end-1c') == 'Also keep these notes'
    assert saved_job(app, first)['follow_up_date'] is None
    assert app.test_errors or 'sav' in app.tracking_notice.get().casefold()
    app.tracking['follow_up_date'].set('2026-10-25')
    wait_for_saved(app, first, follow_up_date='2026-10-25',
                   contact='Keep this unfinished draft', notes='Also keep these notes')


@pytest.mark.parametrize('field', ['application_date', 'follow_up_date', 'interview_dates'])
def test_invalid_date_autosave_keeps_text_and_recovers_after_correction(app, field):
    identifier = save_through_form(app)
    app.tracking[field].set('2026-10-')
    pump(app)
    assert app.tracking[field].get() == '2026-10-'
    assert saved_job(app, identifier)[field] in (None, [])
    assert 'sav' in app.tracking_notice.get().casefold()
    app.tracking[field].set('2026-10-22')
    expected = ['2026-10-22'] if field == 'interview_dates' else '2026-10-22'
    wait_for_saved(app, identifier, **{field: expected})


@pytest.mark.parametrize('action', ['switch', 'refresh', 'filter', 'close'])
def test_failed_database_save_retains_edits_and_blocks_destructive_navigation(app, monkeypatch, action):
    first, second = two_jobs(app)
    update = app.service.update_tracking

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('Synthetic database is locked')

    monkeypatch.setattr(app.service, 'update_tracking', fail)
    app.tracking['contact'].set('Recoverable contact')
    app.notes.insert('1.0', 'Recoverable note')
    pump(app)
    try_navigation(app, action, second, monkeypatch)
    assert app.selected_job['job_id'] == first
    assert app.job_tree.selection() == (first,)
    assert app.tracking['contact'].get() == 'Recoverable contact'
    assert app.notes.get('1.0', 'end-1c') == 'Recoverable note'
    assert saved_job(app, first)['contact'] is None
    monkeypatch.setattr(app.service, 'update_tracking', update)
    app.save_tracking()
    finish(app)
    assert saved_job(app, first)['contact'] == 'Recoverable contact'
    assert saved_job(app, first)['notes'] == 'Recoverable note'


def test_edits_during_another_worker_are_saved_when_it_finishes(app):
    identifier = save_through_form(app)
    release_worker = threading.Event()
    app._run('Synthetic background task', lambda: release_worker.wait(4), lambda _: app.refresh())
    try:
        app.tracking['contact'].set('Entered while the background task was running')
        app.notes.insert('1.0', 'Do not lose edits when the task refreshes the list')
        pump(app)
    finally:
        release_worker.set()
    finish(app)
    wait_for_saved(app, identifier,
                   contact='Entered while the background task was running',
                   notes='Do not lose edits when the task refreshes the list')
    assert not app.test_errors


def test_selection_refresh_and_unchanged_save_do_not_create_history_events(app):
    first, second = two_jobs(app)
    before = {identifier: app.service.history(identifier) for identifier in (first, second)}
    app.refresh()
    app.window.update()
    app.job_tree.selection_set(second)
    app.window.update()
    app.job_tree.selection_set(first)
    app.window.update()
    app.save_tracking()
    finish(app)
    pump(app)
    assert {identifier: app.service.history(identifier) for identifier in (first, second)} == before
    assert not app.test_errors


def test_restoring_saved_value_clears_unsaved_notice_without_writing(app):
    identifier = save_through_form(app)
    before = app.service.history(identifier)
    app.tracking['follow_up_date'].set('2026-10-')
    pump(app)
    assert 'not saved' in app.tracking_notice.get()
    app.tracking['follow_up_date'].set('')
    assert 'not saved' not in app.tracking_notice.get()
    pump(app)
    assert app.service.history(identifier) == before


def test_text_entered_after_explicit_save_is_not_replaced_by_an_old_snapshot(app):
    identifier = save_through_form(app)
    app.tracking['contact'].set('First draft contact')
    app.save_tracking()
    app.tracking['contact'].set('Revised contact entered after clicking Save')
    wait_for_saved(app, identifier, contact='Revised contact entered after clicking Save')
    assert app.tracking['contact'].get() == 'Revised contact entered after clicking Save'


def test_changing_status_does_not_undo_a_pending_date_clear(app):
    identifier = save_through_form(app)
    app.tracking['application_date'].set('2026-09-20')
    app.save_tracking()
    app.tracking['application_date'].set('')
    app.tracking['status'].set('interview')
    app.status_choice.event_generate('<<ComboboxSelected>>')
    assert app.tracking['application_date'].get() == ''
    wait_for_saved(app, identifier, application_date=None, status='interview')


@pytest.mark.parametrize('action', ['history', 'dashboard'])
def test_reports_include_text_entered_just_before_opening_them(app, monkeypatch, action):
    identifier = save_through_form(app)
    app.tracking['next_action'].set('Newly entered follow-up action')
    shown = []
    monkeypatch.setattr(app, '_show_text', lambda *args: shown.append(saved_job(app, identifier)))
    (app.show_history if action == 'history' else app.show_dashboard)()
    assert shown[0]['next_action'] == 'Newly entered follow-up action'


def test_first_text_edit_starts_tracking_an_untracked_job(app):
    record = synthetic_job()
    app.service.db.initialize()
    identifier, _ = app.service.db.upsert_job(record)
    app.refresh()
    app.job_tree.selection_set(identifier)
    app.window.update()
    assert app.selected_job['status'] == 'untracked'
    assert app.service.history(identifier) == []
    app.tracking['contact'].set('Example hiring team')
    wait_for_saved(app, identifier, contact='Example hiring team', status='interested')
    assert len(app.service.history(identifier)) == 1
    assert not app.test_errors
