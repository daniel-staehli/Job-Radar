"""Explicit AI vacancy extraction stays reviewable and independent of generation."""
from copy import deepcopy
import threading
import time

import pytest

from test_gui import app, finish, save_through_form, vacancy
from test_gui_import_concurrency import blocked_action, clean_up


PROSE = (
    "Zur Verstärkung unseres Teams im Grants Office der Example Hochschule "
    "suchen wir eine engagierte Persönlichkeit. Sie beraten Forschende bei "
    "nationalen und internationalen Forschungsförderprogrammen."
)


def test_service_to_form_shows_actual_quoted_evidence_and_saves_only_after_review(app, monkeypatch):
    from job_radar import ai_text_import
    text = "Das Grants Office der Example Hochschule sucht Unterstützung für die Forschungsförderung."
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        return {"company": {"value": "Example Hochschule", "evidence": text},
                "title": {"value": "", "evidence": ""},
                "location": {"value": "", "evidence": ""}}, {}
    monkeypatch.setattr(ai_text_import, "resolve_response_fn", lambda settings: respond)
    app.paste_job_text()
    app.pasted_job_text.insert("1.0", text)
    app.extract_pasted_job_ai()
    finish(app)
    assert len(calls) == 1
    assert not app.test_errors
    assert app.job_fields["company"].get() == "Example Hochschule"
    assert app.job_fields["title"].get() == app.job_fields["location"].get() == ""
    assert app.description.get("1.0", "end-1c") == text
    assert app.draft_path.is_file()
    assert not app.service.db_path.exists()
    shown = []
    monkeypatch.setattr(app, "_show_text", lambda title, contents: shown.append((title, contents)))
    app.show_import_evidence()
    assert len(calls) == 1
    assert text in shown[-1][1]
    assert "Company: Example Hochschule" in shown[-1][1]
    assert set(app.import_unresolved_fields) == {"title", "location"}
    evidence_text = shown[-1][1].lower()
    assert "position" in evidence_text and "location" in evidence_text
    assert "text" in evidence_text and "role type" in evidence_text
    assert "manual" in evidence_text
    status = app.import_task_status.get().lower()
    assert "position" in status and "location" in status
    app.job_fields["title"].set("Reviewed job title")
    app.job_fields["location"].set("Reviewed workplace")
    app.save_job()
    finish(app)
    assert len(app.service.jobs()) == 1
    assert app.service.jobs()[0]["description"] == text
    assert not app.test_errors


def test_new_or_cleared_draft_does_not_show_previous_ai_evidence(app, monkeypatch):
    result = extraction()
    result["ai_evidence"] = {"company": {"value": "Example Hochschule", "quote": PROSE}}
    result["unresolved_fields"] = {
        "title": "Position not extracted in this test. Check the advert heading.",
        "location": "Workplace not confirmed in this test. Check the workplace line.",
    }
    app._fetched(result)
    assert app.import_ai_evidence
    assert app.import_unresolved_fields == result["unresolved_fields"]
    app.clear_import()
    assert not app.import_ai_evidence
    assert app.import_unresolved_fields == {}
    shown = []
    monkeypatch.setattr(app, "_show_text", lambda title, contents: shown.append(contents))
    app.show_import_evidence()
    assert "no AI-suggested fields" in shown[-1]
    assert PROSE not in shown[-1]
    assert "in this test" not in shown[-1]
    app._fetched(result)
    app._loaded(vacancy())
    assert not app.import_ai_evidence
    assert app.import_unresolved_fields == {}


def extraction(*, model_used=True, **changes):
    job = vacancy(company="Example Hochschule", title="", location="", source_url="",
                  description=PROSE, source="pasted text", **changes)
    return {
        "job": job, "model_used": model_used,
        "missing_fields": [key for key in ("company", "title", "location") if not job.get(key)],
        "warnings": ["Check the suggested details before saving."],
        "extraction_method": "ai" if model_used else "labelled_text",
        "complete": all(job.get(key) for key in ("company", "title", "location")),
        "needs_review": True,
    }


def provider_ready(app, monkeypatch):
    monkeypatch.setattr(app, "_require_provider", lambda settings: {"ready": True})


def assert_ai_status(app, used):
    text = app.import_task_status.get().lower()
    assert "ai" in text
    if used:
        assert "no ai" not in text and "without ai" not in text
    else:
        assert "no ai" in text or "without ai" in text


def test_paste_ai_only_runs_after_explicit_click_and_requires_reviewed_save(app, monkeypatch):
    provider_ready(app, monkeypatch)
    calls = []

    def ai_import(text, **kwargs):
        calls.append((text, kwargs))
        return extraction()

    monkeypatch.setattr(app.service, "import_text_with_ai", ai_import)
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", PROSE)
    app.pasted_job_url.insert(0, "https://example.org/jobs/grants")
    app.window.update()
    assert calls == []
    assert not app.service.db_path.exists()

    expected_settings = deepcopy(app.ai_current)
    app.extract_pasted_job_ai()
    finish(app)
    assert len(calls) == 1
    assert calls[0][0] == PROSE
    assert calls[0][1]["source_url"] == "https://example.org/jobs/grants"
    assert calls[0][1]["provider_settings"] == expected_settings
    assert set(calls[0][1]) <= {"source_url", "known_fields", "provider_settings"}
    assert not window.winfo_exists()
    assert app.job_fields["company"].get() == "Example Hochschule"
    assert app.job_fields["title"].get() == ""
    assert app.description.get("1.0", "end-1c") == PROSE
    assert any(label in app.import_note.get().lower() for label in ("title", "position"))
    assert not app.service.db_path.exists()
    assert_ai_status(app, True)
    assert not app.test_errors


def test_local_paste_never_falls_back_to_ai_with_incomplete_information(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local extraction must never consult or call AI")

    app.ai_current = None
    monkeypatch.setattr(app, "_selected_ai", forbidden)
    monkeypatch.setattr(app, "_require_provider", forbidden)
    monkeypatch.setattr(app.service, "import_text_with_ai", forbidden)
    app.paste_job_text()
    app.pasted_job_text.insert("1.0", PROSE)
    app.extract_pasted_job()
    finish(app)
    assert all(not app.job_fields[key].get() for key in ("company", "title", "location"))
    assert app.description.get("1.0", "end-1c") == PROSE
    assert_ai_status(app, False)
    assert not app.service.db_path.exists()
    assert not app.test_errors


def test_ai_form_completion_preserves_reviewed_fields_description_and_discovery_identity(app, monkeypatch):
    provider_ready(app, monkeypatch)
    raw = vacancy(company="Reviewed institution", title="", location="Reviewed location",
                  description=PROSE, source="Reviewed source", role_family="Research support",
                  source_url="https://example.org/jobs/reviewed", language="de", external_id="vacancy-42")
    app._fill_draft(raw)
    candidate = {"id": "candidate_" + "f" * 24, "source": "external feed", "title": "Original title"}
    app.discovery_draft = deepcopy(candidate)
    before = {key: variable.get() for key, variable in app.job_fields.items()}
    calls = []

    def ai_import(text, **kwargs):
        calls.append((text, deepcopy(kwargs)))
        result = extraction()
        result["job"].update(company="Unreviewed replacement", title="Research Funding Officer",
                              location="Guessed city", description="Rewritten text",
                              source="AI replacement", source_url="https://wrong.example/vacancy",
                              role_family="Unreviewed role", external_id="wrong-id")
        result["missing_fields"] = []
        return result

    monkeypatch.setattr(app.service, "import_text_with_ai", ai_import)
    app.extract_import_with_ai()
    finish(app)
    assert len(calls) == 1
    assert calls[0][0] == PROSE
    assert calls[0][1]["source_url"] == before["source_url"]
    known = calls[0][1]["known_fields"]
    assert known["company"] == before["company"]
    assert known["location"] == before["location"]
    for key, value in before.items():
        if value:
            assert app.job_fields[key].get() == value, key
    assert app.job_fields["title"].get() == "Research Funding Officer"
    assert app.description.get("1.0", "end-1c") == PROSE
    assert app.discovery_draft == candidate
    assert app.draft["external_id"] == "vacancy-42"
    assert app.draft["language"] == "de"
    assert not app.service.db_path.exists()
    assert not app.test_errors


@pytest.mark.parametrize("from_paste", [True, False])
def test_failed_ai_extraction_keeps_user_input_and_saved_database(app, monkeypatch, from_paste):
    provider_ready(app, monkeypatch)
    save_through_form(app)
    original_database = app.service.db_path.read_bytes()
    fields = {key: variable.get() for key, variable in app.job_fields.items()}
    description = app.description.get("1.0", "end-1c")

    def fail(*args, **kwargs):
        raise ValueError("Synthetic extraction failed; your input is retained.")

    monkeypatch.setattr(app.service, "import_text_with_ai", fail)
    if from_paste:
        app.paste_job_text()
        window = app._paste_window
        app.pasted_job_text.insert("1.0", PROSE)
        app.pasted_job_url.insert(0, "https://example.org/jobs/grants")
        app.extract_pasted_job_ai()
    else:
        app.extract_import_with_ai()
    finish(app)
    assert {key: variable.get() for key, variable in app.job_fields.items()} == fields
    assert app.description.get("1.0", "end-1c") == description
    assert app.service.db_path.read_bytes() == original_database
    assert app.test_errors == ["Synthetic extraction failed; your input is retained."]
    assert all(str(button["state"]) == "normal" for button in app.import_buttons)
    assert all(str(field["state"]) == "normal" for field in app.import_fields)
    if from_paste:
        assert window.winfo_exists()
        assert app.pasted_job_text.get("1.0", "end-1c") == PROSE
        assert app.pasted_job_url.get() == "https://example.org/jobs/grants"


def test_ai_extraction_captures_settings_and_allows_generation_to_finish_independently(app, monkeypatch):
    provider_ready(app, monkeypatch)
    main_release, import_release, entered = threading.Event(), threading.Event(), threading.Event()
    expected = deepcopy(app.ai_current)
    received = []
    main_results = []

    def ai_import(text, **kwargs):
        entered.set()
        assert import_release.wait(5)
        received.append((text, deepcopy(kwargs)))
        return extraction()

    monkeypatch.setattr(app.service, "import_text_with_ai", ai_import)
    try:
        app._run("Generating test documents", blocked_action(main_release, "generation done"), main_results.append)
        app.paste_job_text()
        app.pasted_job_text.insert("1.0", PROSE)
        app.extract_pasted_job_ai()
        assert entered.wait(5)
        app.ai_current["model"] = "selection-changed-after-start"
        app.ai_current["reasoning_effort"] = "changed-after-start"
        app.extract_pasted_job_ai()
        app.extract_import_with_ai()
        assert app._worker is not None and app._import_worker is not None
        assert all(str(button["state"]) == "disabled" for button in app.import_buttons)
        main_release.set()
        app._worker.join(5)
        app._poll()
        assert main_results == ["generation done"]
        assert app.busy and app._import_worker is not None
        assert all(str(button["state"]) == "normal" for button in app.action_buttons)
        assert not app.service.db_path.exists()
    finally:
        clean_up(app, main_release, import_release)
    assert len(received) == 1
    assert received[0][1]["provider_settings"] == expected
    assert received[0][0] == PROSE
    assert not app.test_errors
    assert_ai_status(app, True)


@pytest.mark.parametrize("from_paste", [True, False])
def test_ai_button_reports_no_ai_when_local_extraction_is_sufficient(app, monkeypatch, from_paste):
    provider_ready(app, monkeypatch)
    result = extraction(model_used=False)
    monkeypatch.setattr(app.service, "import_text_with_ai", lambda *args, **kwargs: result)
    if from_paste:
        app.paste_job_text()
        app.pasted_job_text.insert("1.0", PROSE)
        app.extract_pasted_job_ai()
    else:
        app._fill_draft(vacancy(description=PROSE))
        app.extract_import_with_ai()
    finish(app)
    assert_ai_status(app, False)
    assert not app.test_errors
    assert not app.service.db_path.exists()


def test_invalid_ai_settings_keep_pasted_text_available_for_local_extraction(app, monkeypatch):
    app.ai_current = None
    monkeypatch.setattr(app.service, "import_text_with_ai", lambda *a, **kw: pytest.fail("Invalid settings reached AI"))
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", PROSE)
    app.extract_pasted_job_ai()
    finish(app)
    assert app.test_errors
    assert window.winfo_exists()
    assert app.pasted_job_text.get("1.0", "end-1c") == PROSE
    assert not app.service.db_path.exists()
    app.test_errors.clear()
    app.extract_pasted_job()
    finish(app)
    assert not app.test_errors
    assert not window.winfo_exists()
    assert app.description.get("1.0", "end-1c") == PROSE


def test_paste_dialog_keeps_both_extraction_choices_visible_at_minimum_size(app):
    app.window.attributes("-alpha", 0)
    app.window.deiconify()
    app.paste_job_text()
    window = app._paste_window
    window.attributes("-alpha", 0)
    width, height = window.minsize()
    window.geometry(f"{width}x{height}")
    buttons = [button for button in app.import_buttons if button.winfo_toplevel() == window]
    assert {button["text"] for button in buttons} >= {"Extract details (no AI)", "Extract with AI", "Close"}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.window.update()
        if all(button.winfo_ismapped() and button.winfo_height() > 1 for button in buttons):
            break
        time.sleep(.01)
    left, top = window.winfo_rootx(), window.winfo_rooty()
    right, bottom = left + window.winfo_width(), top + window.winfo_height()
    for button in buttons:
        assert button.winfo_ismapped()
        assert left <= button.winfo_rootx() < right
        assert button.winfo_rootx() + button.winfo_width() <= right
        assert top <= button.winfo_rooty() < bottom
        assert button.winfo_rooty() + button.winfo_height() <= bottom
    assert app.pasted_job_text.winfo_ismapped() and app.pasted_job_text.winfo_height() > 30


def test_repeated_ai_extraction_reports_no_new_fields_and_keeps_manual_details(app, monkeypatch):
    from job_radar import ai_text_import
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        return {name: {"value": "", "evidence": ""}
                for name in ("company", "title", "location")}, {}
    monkeypatch.setattr(ai_text_import, "resolve_response_fn", lambda settings: respond)
    app._fill_draft(vacancy(company="Example Hochschule", title="", location="",
                            description=PROSE, role_family="Research support"))
    app.extract_import_with_ai()
    finish(app)
    assert len(calls) == 1
    assert app.job_fields["company"].get() == "Example Hochschule"
    assert app.job_fields["role_family"].get() == "Research support"
    assert app.job_fields["title"].get() == app.job_fields["location"].get() == ""
    assert app.import_ai_evidence == {}
    assert set(app.import_unresolved_fields) == {"title", "location"}
    message = app.import_note.get().lower()
    assert "no" in message and ("additional" in message or "new" in message or "add" in message)
    status = app.import_task_status.get().lower()
    assert "position" in status and "location" in status
    assert not app.service.db_path.exists() and not app.test_errors
