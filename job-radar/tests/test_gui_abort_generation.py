"""Abort document generation through the real GUI lifecycle, without live AI."""
import threading
import time

import pytest

from test_gui import app, finish, save_through_form
from test_gui_import_concurrency import blocked_action, clean_up


def stopped(app):
    text = app.document_result.get().lower()
    assert "abort" in text or "stopped" in text
    assert app._generation_cancel is None
    assert app._generation_job_id is None
    assert str(app.abort_generation_button["state"]) == "disabled"
    assert not app.test_errors


def successful_result(tmp_path):
    return {"outputs": [], "output_root": tmp_path / "new-success",
            "validation_errors": [], "compile_errors": []}


def cancellable_wait(entered, release=None):
    """Return an action that exits only when its own generation scope is stopped."""
    def wait(*args, **kwargs):
        from job_radar.cancellation import check_cancelled, current_token
        token = current_token()
        assert token is not None, "Generation must own its cancellation scope"
        entered.set()
        deadline = time.monotonic() + 5
        while not token.is_cancelled:
            assert time.monotonic() < deadline, "Test did not abort generation"
            time.sleep(.005)
        if release is not None:
            assert release.wait(5), "Test did not allow cancelled worker cleanup"
        check_cancelled()
        pytest.fail("Cancellation did not stop the generation action")
    return wait


def test_abort_is_disabled_without_generation_and_does_not_start_work(app):
    assert str(app.abort_generation_button["state"]) == "disabled"
    assert app.abort_generation_button not in app.action_buttons
    app.abort_generation()
    assert app._generation_cancel is None
    assert app._worker is None and not app.busy
    assert not app.service.db_path.exists()
    assert not app.test_errors


def test_abort_stops_generation_preserves_previous_output_and_allows_new_run(app, monkeypatch, tmp_path):
    save_through_form(app)
    previous = tmp_path / "earlier-application"
    previous.mkdir()
    original = previous / "cv.tex"
    original.write_text("Existing reviewed CV", encoding="utf-8")
    app.last_output = previous
    refreshed = []
    monkeypatch.setattr(app, "refresh_drafts", lambda **kwargs: refreshed.append(kwargs))
    entered = threading.Event()
    monkeypatch.setattr(app.service, "generate", cancellable_wait(entered))
    try:
        app.create_documents(preview=False)
        assert entered.wait(5)
        assert str(app.abort_generation_button["state"]) == "normal"
        app.abort_generation()
        app.abort_generation()
        finish(app)
    finally:
        if app._generation_cancel is not None:
            app._generation_cancel.cancel()
        finish(app)
    stopped(app)
    assert app.last_output == previous
    assert original.read_text(encoding="utf-8") == "Existing reviewed CV"
    assert refreshed == []
    assert all(str(button["state"]) == "normal" for button in app.action_buttons)
    monkeypatch.setattr(app.service, "generate", lambda *a, **kw: successful_result(tmp_path))
    app.create_documents(preview=False)
    finish(app)
    assert app._generation_cancel is None
    assert str(app.abort_generation_button["state"]) == "disabled"
    assert app.last_output == tmp_path / "new-success"
    assert not app.test_errors


def test_abort_during_preflight_never_calls_generation(app, monkeypatch):
    save_through_form(app)
    entered = threading.Event()
    monkeypatch.setattr(app, "_require_provider", cancellable_wait(entered))
    monkeypatch.setattr(app.service, "generate", lambda *a, **kw: pytest.fail("Aborted preflight generated documents"))
    try:
        app.create_documents(preview=False)
        assert entered.wait(5)
        app.abort_generation()
        finish(app)
    finally:
        if app._generation_cancel is not None:
            app._generation_cancel.cancel()
        finish(app)
    stopped(app)


def test_abort_keeps_controls_locked_until_worker_has_really_exited(app, monkeypatch):
    save_through_form(app)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(app.service, "generate", cancellable_wait(entered, release))
    try:
        app.create_documents(preview=False)
        assert entered.wait(5)
        started = time.monotonic()
        app.abort_generation()
        assert time.monotonic() - started < .5, "Abort must not wait on the GUI thread"
        app.window.update()
        assert app.busy and app._worker is not None
        assert app._worker.is_alive()
        assert str(app.abort_generation_button["state"]) == "disabled"
        assert all(str(button["state"]) == "disabled" for button in app.action_buttons)
        assert "stop" in (app.document_result.get() + app.status.get()).lower()
        app._close()
        assert app.window.winfo_exists() and app.test_notices
    finally:
        if app._generation_cancel is not None:
            app._generation_cancel.cancel()
        clean_up(app, release)
    stopped(app)


def test_abort_does_not_cancel_independent_import(app, monkeypatch):
    save_through_form(app)
    entered, import_release = threading.Event(), threading.Event()
    imported = []
    monkeypatch.setattr(app.service, "generate", cancellable_wait(entered))
    try:
        app._run_import("Importing test vacancy", blocked_action(import_release, "import complete"), imported.append)
        app.create_documents(preview=False)
        assert entered.wait(5)
        app.abort_generation()
        app._worker.join(5)
        app._poll()
        stopped(app)
        assert app._worker is None
        assert app._import_worker is not None and app._import_worker.is_alive()
        assert app.busy and not imported
        assert all(str(button["state"]) == "disabled" for button in app.import_buttons)
        assert all(str(button["state"]) == "normal" for button in app.action_buttons)
    finally:
        if app._generation_cancel is not None:
            app._generation_cancel.cancel()
        clean_up(app, import_release)
    assert imported == ["import complete"]
    assert not app.test_errors


def test_abort_after_worker_finishes_but_before_ui_accepts_result_discards_result(app, monkeypatch, tmp_path):
    save_through_form(app)
    previous = tmp_path / "earlier-output"
    app.last_output = previous
    monkeypatch.setattr(app.service, "generate", lambda *a, **kw: successful_result(tmp_path))
    app.create_documents(preview=False)
    worker = app._worker
    worker.join(5)
    assert not worker.is_alive()
    # Do not pump Tk yet: the completed result is queued but has not been accepted.
    assert app._generation_cancel is not None
    app.abort_generation()
    finish(app)
    stopped(app)
    assert app.last_output == previous


def test_preview_and_unrelated_work_are_not_cancellable_generation(app, monkeypatch, tmp_path):
    save_through_form(app)
    release = threading.Event()
    preview_result = successful_result(tmp_path)
    monkeypatch.setattr(app.service, "preview", lambda *a, **kw: blocked_action(release, preview_result)())
    monkeypatch.setattr(app.service, "review_text", lambda result: "Synthetic preview")
    shown = []
    monkeypatch.setattr(app, "_show_text", lambda title, text: shown.append(text))
    try:
        app.create_documents(preview=True)
        assert app._generation_cancel is None
        assert str(app.abort_generation_button["state"]) == "disabled"
        app.abort_generation()
        assert app.busy
    finally:
        clean_up(app, release)
    assert shown == ["Synthetic preview"]
    assert not app.test_errors
    release = threading.Event()
    completed = []
    try:
        app._run("Other local work", blocked_action(release, "done"), completed.append)
        app.abort_generation()
        assert app._generation_cancel is None
        assert str(app.abort_generation_button["state"]) == "disabled"
    finally:
        clean_up(app, release)
    assert completed == ["done"]


def test_regular_generation_error_resets_abort_button_and_is_still_reported(app, monkeypatch):
    save_through_form(app)
    def fail(*args, **kwargs):
        raise ValueError("Synthetic non-cancellation failure")
    monkeypatch.setattr(app.service, "generate", fail)
    app.create_documents(preview=False)
    finish(app)
    assert app._generation_cancel is None
    assert app._generation_job_id is None
    assert str(app.abort_generation_button["state"]) == "disabled"
    assert app.test_errors == ["Synthetic non-cancellation failure"]


def test_abort_button_remains_visible_at_minimum_window_size(app):
    app.window.attributes("-alpha", 0)
    app.window.geometry("960x680")
    app.tabs.select(app.docs_tab)
    app.window.deiconify()
    button = app.abort_generation_button
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.window.update()
        if button.winfo_ismapped() and button.winfo_height() > 1:
            break
        time.sleep(.01)
    assert button.winfo_ismapped() and button.winfo_height() > 1
    assert button.winfo_rooty() + button.winfo_height() <= app.window.winfo_rooty() + app.window.winfo_height()
    assert button.winfo_rootx() + button.winfo_width() <= app.window.winfo_rootx() + app.window.winfo_width()


def test_completion_error_releases_generation_state_and_allows_retry(app, monkeypatch, tmp_path):
    from job_radar import gui
    save_through_form(app)
    previous = tmp_path / "earlier-output"
    app.last_output = previous
    monkeypatch.setattr(app.service, "generate", lambda *a, **kw: successful_result(tmp_path))
    original_finish = gui.finish_outputs
    def fail_completion(token):
        raise OSError("Synthetic output marker write failure")
    monkeypatch.setattr(gui, "finish_outputs", fail_completion)
    app.create_documents(preview=False)
    finish(app)
    assert app._generation_cancel is None
    assert app._generation_job_id is None
    assert str(app.abort_generation_button["state"]) == "disabled"
    assert app.last_output == previous
    assert app.test_errors == ["Synthetic output marker write failure"]
    assert all(str(button["state"]) == "normal" for button in app.action_buttons)
    monkeypatch.setattr(gui, "finish_outputs", original_finish)
    app.test_errors.clear()
    app.create_documents(preview=False)
    finish(app)
    assert app.last_output == tmp_path / "new-success"
    assert app._generation_cancel is None
    assert not app.test_errors


def test_small_window_scroll_reveals_reference_lists_and_keeps_abort_available(app):
    app.references = [
        {"id": "example-cv", "label": "Example CV", "source_name": "example-cv.txt", "document_type": "cv"},
        {"id": "example-letter", "label": "Example letter", "source_name": "example-letter.txt", "document_type": "cover_letter"},
    ]
    app._refresh_document_references()
    app.window.attributes("-alpha", 0)
    app.window.geometry("960x680")
    app.tabs.select(app.docs_tab)
    app.window.deiconify()
    app.window.update()
    canvas = app._document_canvas
    canvas.yview_moveto(1)
    app.window.update()
    top, bottom = canvas.winfo_rooty(), canvas.winfo_rooty() + canvas.winfo_height()
    for listing in (app.doc_refs, app.doc_selected_refs):
        assert listing.winfo_ismapped()
        assert listing.winfo_height() >= 70, "References must stay large enough to choose documents"
        assert top <= listing.winfo_rooty()
        assert listing.winfo_rooty() + listing.winfo_height() <= bottom
    abort = app.abort_generation_button
    assert abort.winfo_ismapped() and abort.winfo_height() > 1
    assert abort.winfo_rooty() >= bottom
    assert abort.winfo_rooty() + abort.winfo_height() <= app.window.winfo_rooty() + app.window.winfo_height()
    app.doc_refs.selection_set(0)
    add = next(button for button in app.action_buttons if button["text"] == "Add →")
    add.invoke()
    app.window.update()
    assert app.chosen_reference_ids == ["example-cv"]
    assert app.doc_selected_refs.size() == 1
    assert "Example CV" in app.doc_selected_refs.get(0)
    assert app.reference_selection_count.get().endswith("(1/3)")
    assert not app.test_errors
