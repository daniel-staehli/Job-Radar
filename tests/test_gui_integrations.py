"""Exercise the new desktop workflows using synthetic records and fake services."""
from datetime import date
from pathlib import Path
import gc
import json
import socket
import time
import tkinter as tk

import httpx
import pytest
import yaml

from job_radar import codex_client, cv, discovery, gui, providers, revisions
from job_radar.gui_services import GuiService
from test_revisions import application, profile


@pytest.fixture
def app(tmp_path, monkeypatch, tk_session_root):
    def denied(*args, **kwargs):
        raise AssertionError("GUI integration test attempted a real network or model call")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(providers, "_post", denied)
    monkeypatch.setattr(codex_client, "structured_response", denied)
    monkeypatch.setattr(cv, "codex_response", denied)
    monkeypatch.setattr(cv, "api_response", denied)
    monkeypatch.setattr(cv, "fetch_job_page_text", denied)
    monkeypatch.setattr(revisions, "codex_response", denied)
    monkeypatch.setattr(revisions, "api_response", denied)
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, message, **kw: errors.append(message))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args, **kwargs: None)
    # Dispose callbacks from earlier destroyed Tk roots before creating another
    # interpreter; otherwise their finalizers can run during Tcl startup.
    gc.collect()
    try:
        window = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    window.withdraw()
    instance = gui.JobRadarApp(window, GuiService(tmp_path))
    instance.test_errors = errors
    try:
        yield instance
    finally:
        if not instance._closed:
            from test_gui import finish as finish_worker
            finish_worker(instance)
            window.destroy()


def finish(app):
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        app.window.update()
        time.sleep(0.01)
    app.window.update()
    assert not app.busy, "GUI worker did not complete"
    assert not app.test_errors


def posting(number, **changes):
    return {"id": number, "title": "Research Coordinator", "location": {"name": "Lausanne"},
            "absolute_url": f"https://job-boards.greenhouse.io/fictional/jobs/{number}",
            "content": "<p>Coordinate research experiments and laboratory samples.</p>", **changes}


def save_employer(app, board="fictional"):
    return app.service.save_employer({"company": "Fictional Lab", "provider": "greenhouse", "board": board})


def candidate(app, number, **changes):
    preset = save_employer(app)
    return discovery._candidate(preset, posting(number, **changes), "2026-10-01T08:00:00Z")


def manual_job(number, **changes):
    return {"company": "Synthetic Lab", "title": f"Research role {number}", "location": "Lausanne",
            "source_url": f"https://example.invalid/jobs/{number}",
            "description": "Coordinate experiments and laboratory samples.", **changes}


def test_employer_refresh_caches_selected_board_and_only_selected_save_imports(app, monkeypatch):
    chosen = save_employer(app)
    save_employer(app, "other-board")
    boards = []

    def fetch(preset):
        boards.append(preset["id"])
        return {"jobs": [posting(101), posting(102)]}

    monkeypatch.setattr(discovery, "_fetch", fetch)
    app._load_discovery()
    app.employer_choice.current(next(i + 1 for i, value in enumerate(app.presets) if value["id"] == chosen["id"]))
    app.refresh_discovery()
    finish(app)
    assert boards == [chosen["id"]]
    assert len(app.candidate_tree.get_children()) == 2
    assert not app.service.db_path.exists()
    selected = next(value for value in app.candidates if value["external_id"] == "102")
    app.candidate_tree.selection_set(selected["id"])
    app._show_candidate()
    assert "jobs/102" in app.candidate_viewer.get("1.0", "end-1c")
    app.save_candidate()
    finish(app)
    saved = app.service.jobs()
    assert len(saved) == 1
    assert saved[0]["source_url"].endswith("/102")
    assert saved[0]["job_id"] == selected["id"].replace("candidate_", "job_ext_", 1)
    assert app.selected_job["job_id"] == saved[0]["job_id"]
    assert boards == [chosen["id"]]
    # Reopening cached results does not refresh or import the other vacancy.
    app._load_discovery()
    assert len(app.candidates) == 2 and len(app.service.jobs()) == 1
    assert boards == [chosen["id"]]


def test_candidate_manual_review_keeps_external_identity_and_application_history(app, monkeypatch):
    incomplete = candidate(app, 203, location=None, content=None)
    monkeypatch.setattr(app.service, "discovery_candidates", lambda: [incomplete])
    app._load_discovery()
    app.candidate_tree.selection_set(incomplete["id"])
    app.edit_candidate()
    assert app.tabs.select() == str(app.import_tab)
    assert app.discovery_draft["external_id"] == "203"
    app.job_fields["title"].set("Reviewed research role")
    app.job_fields["location"].set("Zurich")
    app.description.insert("1.0", "Coordinate experiments with a research team.")
    app.save_job()
    finish(app)
    identifier = incomplete["id"].replace("candidate_", "job_ext_", 1)
    assert app.selected_job["job_id"] == identifier
    app.service.update_tracking(identifier, {"status": "applied", "cv_version": "reviewed-v1.pdf"})
    before = app.service.history(identifier)
    # A later review of the same posting updates vacancy text, not tracking.
    app.edit_candidate()
    app.job_fields["location"].set("Lausanne")
    app.description.insert("1.0", "Updated employer description.")
    app.save_job()
    finish(app)
    saved = app.service.jobs()
    assert len(saved) == 1 and saved[0]["job_id"] == identifier
    assert saved[0]["location"] == "Lausanne" and saved[0]["status"] == "applied"
    assert saved[0]["cv_version"] == "reviewed-v1.pdf"
    assert app.service.history(identifier) == before


def test_api_choice_generates_and_improves_with_selected_provider_without_codex_check(app, monkeypatch, tmp_path):
    value = {**providers.default_settings("anthropic"), "model": "synthetic-model"}
    app.service.save_ai_settings(value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")
    monkeypatch.setattr(app.service, "check_codex", lambda: (_ for _ in ()).throw(AssertionError("Codex must not be checked")))
    app._update_ai_labels()
    assert "Anthropic API" in app.ai_summary.get()
    identifier, _ = app.service.save_job(manual_job(301))
    app.refresh()
    app.document_job.current(next(i for i, job in enumerate(app.jobs) if job["job_id"] == identifier))
    (tmp_path / "data/master_profile.yaml").write_text(yaml.safe_dump(profile()), encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "cv_default.tex.j2").write_text("((( profile_text | tex )))", encoding="utf-8")
    (templates / "motivation_letter.tex.j2").write_text("((( letter.subject | tex )))", encoding="utf-8")
    calls = []

    def adapter(settings):
        assert settings == value
        def respond(**request):
            calls.append(request["schema_name"])
            result = application()
            if request["schema_name"] == "revised_application":
                result.profile = "Researcher with experience planning experiments and coordinating samples."
            return result, {"provider_settings": providers.provider_identity(settings)}
        return respond

    monkeypatch.setattr(cv, "resolve_response_fn", adapter)
    monkeypatch.setattr(revisions, "resolve_response_fn", adapter)
    app.create_documents(preview=False)
    finish(app)
    source = app._chosen_draft()["directory"]
    source_cv = (source / "cv.tex").read_bytes()
    source_letter = (source / "motivation_letter.tex").read_bytes()
    assert json.loads((source / "generation_inputs.json").read_text())["provider"] == "anthropic"
    app.review_document.set("cv")
    app.revision_feedback.insert("1.0", "Clarify the opening.")
    app.improve_document()
    finish(app)
    revised = app._chosen_draft()["directory"]
    assert revised == source
    assert calls == ["tailored_application", "revised_application"]
    assert json.loads((revised / "revision.json").read_text())["provider"] == "anthropic"
    assert (revised / "motivation_letter.tex").read_bytes() == source_letter
    assert (revised / "cv.tex").read_bytes() != source_cv
    audit = json.loads((source / "revision.json").read_text(encoding="utf-8"))
    archived = Path(audit["source_dir"])
    assert archived != source
    assert archived.is_relative_to(tmp_path / "applications" / ".history")
    assert (archived / "cv.tex").read_bytes() == source_cv
    assert (archived / "motivation_letter.tex").read_bytes() == source_letter
    assert app.revision_feedback.get("1.0", "end-1c") == ""


def test_duplicate_title_document_selection_survives_reordered_refresh(app, monkeypatch):
    identities = [app.service.save_discovered_job(candidate(app, number))[0] for number in (401, 402)]
    app.refresh()
    labels = tuple(app.document_job["values"])
    assert len(labels) == len(set(labels)) == 2
    app.document_job.current(next(i for i, job in enumerate(app.jobs) if job["job_id"] == identities[1]))
    old_index = app.document_job.current()
    reordered = list(reversed(app.service.jobs()))
    monkeypatch.setattr(app.service, "jobs", lambda: list(reordered))
    app.refresh()
    assert app.document_job.current() != old_index
    assert app.jobs[app.document_job.current()]["job_id"] == identities[1]
    assert not app.test_errors


def test_local_priority_ranking_counts_alternatives_once_and_keeps_tracker_empty(app):
    first = candidate(app, 601, content='<p>Microscopy with Python and R.</p>')
    second = candidate(app, 602, content='<p>Finance operations and reporting.</p>')
    app.candidates = [second, first]
    app.priority_terms.set('microscopy | protein; R | Python')
    app.rank_candidates()
    assert app.candidate_tree.get_children() == (first['id'], second['id'])
    assert app.candidate_tree.set(first['id'], 'terms') == '2/2'
    app.candidate_tree.selection_set(first['id'])
    app._show_candidate()
    text = app.candidate_viewer.get('1.0', 'end-1c')
    assert 'Priority groups mentioned: 2/2' in text
    assert 'Not mentioned: None' in text
    assert not app.service.db_path.exists()
    assert (app.service.root / 'data/discovery_preferences.json').exists()
    assert not app.test_errors


def test_status_filters_and_metrics_exclude_finished_followups(app, monkeypatch):
    monkeypatch.setattr(gui, "local_today", lambda: date(2026, 10, 1))
    identities = {}
    for number, status in enumerate(("interested", "preparing", "applied", "interview", "rejected", "withdrawn", "offer"), 501):
        identifier, _ = app.service.save_job(manual_job(number))
        app.service.update_tracking(identifier, {"status": status, "follow_up_date": "2026-09-30"})
        identities[status] = identifier
    app.refresh()
    assert app.metrics["saved"].get() == "7"
    assert app.metrics["active"].get() == "3"
    assert app.metrics["due"].get() == "4"
    app.status_filter.set("interview")
    assert app.job_tree.get_children() == (identities["interview"],)
    app.job_tree.selection_set(identities["interview"])
    app._job_selected()
    app.status_filter.set("rejected")
    assert app.job_tree.get_children() == (identities["rejected"],)
    assert app.selected_job is None
    assert app.metrics["due"].get() == "4"
    app.status_filter.set("All statuses")
    assert len(app.job_tree.get_children()) == 7
    assert not app.test_errors


def test_ollama_matches_sort_cached_vacancies_and_explain_results_without_tracking(app, monkeypatch):
    from job_radar import discovery_match, job_match
    from test_discovery_match import assessment
    first, second = candidate(app, 701), candidate(app, 702)
    (app.service.root / 'data/master_profile.yaml').write_text(
        yaml.safe_dump({'skills': [{'text': 'Research coordination'}]}), encoding='utf-8')
    model_calls = []
    def resolver(settings):
        def respond(**kwargs):
            model_calls.append(kwargs)
            result = assessment(category='strong' if len(model_calls) == 2 else 'stretch')
            result.requirements[0].job_quote = 'Coordinate research experiments'
            return result, {}
        return respond
    monkeypatch.setattr(job_match, 'resolve_response_fn', resolver)
    settings = {**providers.default_settings('ollama'), 'model': 'test-local'}
    result = discovery_match.review_candidates(app.service.root, [first, second], settings)
    assert not result['errors']
    app.candidates = [first, second]
    app.discovery_ai_sort.set(True)
    app._fill_candidates()
    assert app.candidate_tree.get_children() == (second['id'], first['id'])
    assert app.candidate_tree.set(second['id'], 'match') == 'Strong'
    app.candidate_tree.selection_set(second['id'])
    app._show_candidate()
    assert 'test-local' in app.candidate_viewer.get('1.0', 'end-1c')
    displayed = []
    monkeypatch.setattr(app, '_show_text', lambda title, text: displayed.append(text))
    app.show_candidate_match()
    assert 'Requirements and gaps' in displayed[0]
    assert len(model_calls) == 2
    assert not app.service.db_path.exists()

