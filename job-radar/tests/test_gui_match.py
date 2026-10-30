"""Exercise AI-only match review from the saved Jobs panel."""
from test_gui import app, finish, save_through_form
from job_radar import job_match


def test_open_match_is_local_and_saved_review_is_readable(app, monkeypatch):
    save_through_form(app)
    calls = []
    monkeypatch.setattr(job_match, "assess_job", lambda *a: calls.append(a))
    monkeypatch.setattr(job_match, "latest_assessment", lambda *a: None)
    app.open_job_match()
    assert not calls
    assert "No match review saved" in app.match_viewer.get("1.0", "end-1c")
    assert "allowance" in app.match_notice.get()
    app.match_window.destroy()
    assert not app.test_errors


def test_match_uses_selected_job_and_current_provider_without_generation(app, monkeypatch):
    job_id = save_through_form(app)
    result = {"assessment": {"category": "plausible"}}
    saved, requests = [], []
    monkeypatch.setattr(job_match, "latest_assessment", lambda *a: saved[-1] if saved else None)
    monkeypatch.setattr(job_match, "format_assessment", lambda r: "Plausible match; review the gaps.")
    def assess(root, job, settings):
        requests.append((job, settings))
        saved.append(result)
        return result
    monkeypatch.setattr(job_match, "assess_job", assess)
    monkeypatch.setattr(app.service, "generate", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Must not generate")))
    app.open_job_match()
    app._run_match_review()
    finish(app)
    assert requests[0][0]["job_id"] == job_id
    assert requests[0][1] == app.ai_current
    assert "Plausible match" in app.match_viewer.get("1.0", "end-1c")
    assert app.service.jobs()[0]["status"] == "interested"
    assert not (app.service.root / "applications").exists()
    assert not app.test_errors
    app.match_window.destroy()


def test_match_blocks_unsaved_profile_and_reports_failed_review(app, monkeypatch):
    save_through_form(app)
    monkeypatch.setattr(job_match, "latest_assessment", lambda *a: None)
    requests = []
    monkeypatch.setattr(job_match, "assess_job", lambda *a: requests.append(a))
    app.open_job_match()
    monkeypatch.setattr(app, "_profile_has_unsaved_changes", lambda: True)
    app._run_match_review()
    assert "Save your changes in My profile" in app.test_errors.pop()
    assert not requests
    monkeypatch.setattr(app, "_profile_has_unsaved_changes", lambda: False)
    def fail(*args):
        raise ValueError("Provider unavailable")
    monkeypatch.setattr(job_match, "assess_job", fail)
    app._run_match_review()
    finish(app)
    assert "Provider unavailable" in app.test_errors.pop()
    assert "earlier saved review is unchanged" in app.match_notice.get()
    app.match_window.destroy()


def test_closing_review_during_request_does_not_break_main_window(app, monkeypatch):
    save_through_form(app)
    monkeypatch.setattr(job_match, "latest_assessment", lambda *a: None)
    monkeypatch.setattr(job_match, "assess_job", lambda *a: {})
    app.open_job_match()
    app._run_match_review()
    app.match_window.destroy()
    finish(app)
    assert not app.test_errors


def test_document_creation_blocks_unsaved_profile(app, monkeypatch):
    save_through_form(app)
    monkeypatch.setattr(app, "_profile_has_unsaved_changes", lambda: True)
    app.create_documents(preview=True)
    assert "Save your changes in My profile" in app.test_errors.pop()
    assert not app.busy
