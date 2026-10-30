"""Import work can progress independently without racing Tk or document inputs."""
import threading
import weakref

import pytest

from job_radar import gui_import
from test_gui import app, finish, save_through_form, vacancy


def blocked_action(release, value):
    def action():
        assert release.wait(5), "Test did not release the worker"
        return value
    return action


def clean_up(app, *events):
    for event in events:
        event.set()
    for worker in (app._worker, app._import_worker):
        if worker is not None:
            worker.join(5)
    finish(app)


@pytest.mark.parametrize("first_finished", ["main", "import"])
@pytest.mark.parametrize("first_started", ["main", "import"])
def test_independent_lanes_finish_in_either_order(app, first_finished, first_started):
    releases = {name: threading.Event() for name in ("main", "import")}
    completed = []
    runners = {"main": app._run, "import": app._run_import}
    order = [first_started, "import" if first_started == "main" else "main"]
    try:
        for name in order:
            runners[name](name, blocked_action(releases[name], name), completed.append)
        assert app.busy
        assert all(str(button['state']) == 'disabled' for button in app.action_buttons)
        assert all(str(button['state']) == 'disabled' for button in app.import_buttons)
        releases[first_finished].set()
        worker = app._worker if first_finished == 'main' else app._import_worker
        worker.join(5)
        assert not worker.is_alive()
        app._poll()
        assert completed == [first_finished]
        assert app.busy
        assert all(str(button['state']) == ('normal' if first_finished == 'main' else 'disabled')
                   for button in app.action_buttons)
        assert all(str(button['state']) == ('normal' if first_finished == 'import' else 'disabled')
                   for button in app.import_buttons)
        assert str(app.progress.cget('mode')) == 'indeterminate'
        app._close()
        assert app.window.winfo_exists()
        assert app.test_notices
    finally:
        clean_up(app, *releases.values())
    assert sorted(completed) == ['import', 'main']
    assert app._worker is None and app._import_worker is None
    assert app._active_action is None and app._import_action is None
    assert all(str(button['state']) == 'normal' for button in app.action_buttons + app.import_buttons)
    assert not app.test_errors


@pytest.mark.parametrize("failed_lane", ["main", "import"])
def test_failure_in_one_lane_does_not_release_the_other(app, failed_lane):
    release = threading.Event()
    completed, failures = [], []
    runners = {'main': app._run, 'import': app._run_import}
    healthy_lane = 'import' if failed_lane == 'main' else 'main'
    def fail():
        raise ValueError('Synthetic ' + failed_lane + ' error')
    try:
        runners[healthy_lane]('Waiting', blocked_action(release, 'healthy'), completed.append)
        runners[failed_lane]('Failing', fail, lambda _: pytest.fail('Failure ran success callback'),
                             lambda error: failures.append(str(error)))
        failed_worker = app._worker if failed_lane == 'main' else app._import_worker
        failed_worker.join(5)
        app._poll()
        assert failures == ['Synthetic ' + failed_lane + ' error']
        assert app.test_errors == failures
        assert completed == [] and app.busy
        app._close()
        assert app.window.winfo_exists()
    finally:
        clean_up(app, release)
    assert completed == ['healthy']


def test_import_locks_form_but_main_work_does_not(app, monkeypatch):
    release_main, release_import = threading.Event(), threading.Event()
    app._fill_draft(vacancy())
    original_description = app.description.get('1.0', 'end-1c')
    monkeypatch.setattr('job_radar.gui.filedialog.askopenfilename',
                        lambda **kw: pytest.fail('Locked import opened a file chooser'))
    try:
        app._run('Generating', blocked_action(release_main, None), lambda _: None)
        assert all(str(field['state']) == 'normal' for field in app.import_fields)
        assert all(str(button['state']) == 'normal' for button in app.import_buttons)
        app._run_import('Fetching', blocked_action(release_import, None), lambda _: None)
        assert all(str(field['state']) == 'disabled' for field in app.import_fields)
        app.description.insert('end', 'must not appear')
        app.clear_import()
        app.load_yaml_file()
        app.paste_job_text()
        assert app._paste_window is None
        with pytest.raises(ValueError, match='Wait for the current vacancy import'):
            app._fill_draft(vacancy(company='Overwritten'))
        assert app.description.get('1.0', 'end-1c') == original_description
        assert app.job_fields['company'].get() == 'Example Bio'
        assert app._run_import('Second', lambda: pytest.fail('Second import ran'), lambda _: None) is False
    finally:
        clean_up(app, release_main, release_import)
    assert all(str(field['state']) == 'normal' for field in app.import_fields)


def test_import_completion_waits_for_worker_exit_before_callbacks_or_close(app, monkeypatch):
    queued, allow_exit = threading.Event(), threading.Event()
    thread_class = threading.Thread
    callbacks = []
    class DelayedExitThread(thread_class):
        def run(self):
            super().run()
            queued.set()
            assert allow_exit.wait(5), 'Test did not release its worker'
    monkeypatch.setattr(gui_import.threading, 'Thread', DelayedExitThread)
    app._run_import('Finishing', lambda: 'result',
                    lambda result: callbacks.append((result, threading.get_ident())))
    assert queued.wait(5)
    try:
        app._poll()
        assert app.busy and app._import_worker is not None
        assert callbacks == []
        app._close()
        assert app.window.winfo_exists() and app.test_notices
    finally:
        clean_up(app, allow_exit)
    assert callbacks == [('result', threading.get_ident())]


def test_import_action_is_released_on_ui_thread(app):
    released_on = []
    class Action:
        def __call__(self):
            return 'complete'
    action = Action()
    weakref.finalize(action, lambda: released_on.append(threading.get_ident()))
    app._run_import('Local action', action, lambda _: None)
    del action
    app._import_worker.join(5)
    assert released_on == []
    finish(app)
    assert released_on == [threading.get_ident()]
    assert app._import_worker is None and app._import_action is None


def test_closed_window_cannot_start_import(app):
    app.window.destroy()
    assert app._closed
    assert app._run_import('Closed', lambda: pytest.fail('Closed window started import'), lambda _: None) is False
    app._poll()
    assert app.window.tk.splitlist(app.window.tk.call('after', 'info')) == ()


def test_saving_new_job_during_generation_keeps_original_selection(app, monkeypatch, tmp_path):
    original_id = save_through_form(app)
    app.documents_for_selected()
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(app, '_require_provider', lambda settings: {'ready': True})
    def generate(job_id, references, **kwargs):
        calls.append(job_id)
        entered.set()
        assert release.wait(5), 'Test did not release generation'
        return {'output_root': tmp_path / 'applications' / 'synthetic', 'outputs': []}
    monkeypatch.setattr(app.service, 'generate', generate)
    try:
        app.create_documents(preview=False)
        assert entered.wait(5)
        app._fill_draft(vacancy(company='Another employer', title='New vacancy', source_url='https://example.org/jobs/two'))
        app.save_job()
        assert app._import_worker is not None
        app._import_worker.join(5)
        app._poll()
        assert len(app.service.jobs()) == 2
        assert app.selected_job['job_id'] != original_id
        assert app.jobs[app.document_job.current()]['job_id'] == original_id
        assert app._worker is not None and app.busy
        assert calls == [original_id]
        assert not app.test_errors
    finally:
        clean_up(app, release)
    assert 'Drafts ready' in app.document_result.get()


def test_cannot_update_the_vacancy_currently_used_for_generation(app, monkeypatch, tmp_path):
    original_id = save_through_form(app)
    release = threading.Event()
    monkeypatch.setattr(app, '_require_provider', lambda _: {'ready': True})
    monkeypatch.setattr(app.service, 'generate', blocked_action_adapter(release, tmp_path))
    try:
        app.create_documents(preview=False)
        assert app._generation_job_id == original_id
        app.description.insert('end', ' Updated text')
        app.save_job()
        assert app._import_worker is None
        assert 'being used to generate' in app.test_errors.pop()
        assert app.service.jobs()[0]['description'] == vacancy()['description']
    finally:
        clean_up(app, release)
    assert app._generation_job_id is None


def blocked_action_adapter(release, tmp_path):
    def generate(*args, **kwargs):
        assert release.wait(5)
        return {'output_root': tmp_path / 'applications', 'outputs': []}
    return generate


def test_generation_waits_for_its_own_vacancy_save_but_not_other_imports(app, monkeypatch):
    original_id = save_through_form(app)
    release = threading.Event()
    real_save = app.service.save_job
    def save(raw):
        assert release.wait(5)
        return real_save(raw)
    monkeypatch.setattr(app.service, 'save_job', save)
    try:
        app.save_job()
        assert app._import_job_id == original_id
        app.create_documents(preview=False)
        assert app._worker is None
        assert 'still being saved' in app.test_errors.pop()
    finally:
        clean_up(app, release)
    assert app._import_job_id is None


def test_external_candidate_guard_uses_the_saved_external_identity(app, monkeypatch):
    existing_id = save_through_form(app)
    digest = 'a' * 24
    app.discovery_draft = {'id': 'candidate_' + digest}
    app._generation_job_id = 'job_ext_' + digest
    app.save_job()
    assert app._import_worker is None
    assert 'being used to generate' in app.test_errors.pop()
    app._generation_job_id = None
    calls = []
    monkeypatch.setattr(app.service, 'save_discovered_job',
                        lambda candidate, raw: (calls.append(candidate) or (existing_id, 'unchanged')))
    app.save_job()
    finish(app)
    assert calls == [{'id': 'candidate_' + digest}]
    assert not app.test_errors


def test_generation_completion_does_not_move_user_away_from_import_form(app, monkeypatch, tmp_path):
    save_through_form(app)
    app.documents_for_selected()
    release = threading.Event()
    monkeypatch.setattr(app, '_require_provider', lambda _: {'ready': True})
    def generate(*args, **kwargs):
        assert release.wait(5)
        return {'output_root': tmp_path / 'applications', 'outputs': [tmp_path / 'applications' / 'draft']}
    monkeypatch.setattr(app.service, 'generate', generate)
    try:
        app.create_documents(preview=False)
        app.tabs.select(app.import_tab)
        app.job_fields['company'].set('New vacancy in progress')
    finally:
        clean_up(app, release)
    assert app.tabs.select() == str(app.import_tab)
    assert app.job_fields['company'].get() == 'New vacancy in progress'
    assert not app.test_errors
