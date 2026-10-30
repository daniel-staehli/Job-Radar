"""Job-match reviews use saved facts, explicit AI calls and private local storage."""
from copy import deepcopy
import json

import pytest
import yaml

from job_radar import job_match as match


JOB = {
    "job_id": "job_0123456789ab", "company": "Example Labs", "title": "Research Coordinator",
    "location": "Example City", "source_url": "https://example.org/jobs/1",
    "description": "Required: coordinate experiments. Python is preferred. Fluent German required.",
    "status": "interested", "notes": "Personal tracking note, not a qualification",
}
SETTINGS = {"provider": "ollama", "model": "example-model", "ollama_thinking": False}
PROFILE = {
    "candidate_summary": "Researcher experienced in coordinating experiments.",
    "contact": {"name": "Test Candidate", "email": "private@example.org", "phone": "private-phone",
                "location": "Example City", "photo_path": "private-photo.jpg"},
    "experiences": [{"key": "research", "role": "Researcher", "dates": "2020-2024",
                     "bullets": [{"id": "coordinated", "text": "Coordinated experiments."}]}],
    "languages": ["German: fluent"], "skills": [{"id": "python", "text": "Basic Python"}],
    "never_claim": [{"pattern": "advanced Python"}],
}


def response():
    return {
        "category": "plausible", "summary": "Documented coordination experience is relevant.",
        "strengths": [{"explanation": "Coordinated experiments.", "evidence_ids": ["experiences/0/bullets/0"]}],
        "requirements": [
            {"requirement": "Coordinate experiments", "job_quote": "Required: coordinate experiments.",
             "importance": "required", "assessment": "supported", "explanation": "Documented in the research role.",
             "evidence_ids": ["experiences/0/bullets/0"]},
            {"requirement": "Python", "job_quote": "Python is preferred.", "importance": "preferred",
             "assessment": "partial", "explanation": "Basic Python only is documented.", "evidence_ids": ["skills/0"]},
        ],
        "uncertainties": ["Confirm the expected Python level."], "next_actions": ["Ask about the typical coordination workload."],
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "master_profile.yaml"
    path.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    calls = []
    def resolve(settings):
        calls.append({"settings": settings})
        def respond(**kwargs):
            calls.append(kwargs)
            return response(), {"unexpected_server_field": "should-not-be-saved"}
        return respond
    monkeypatch.setattr(match, "resolve_response_fn", resolve)
    return tmp_path, path, calls


def test_empty_read_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(match, "resolve_response_fn", lambda *_: pytest.fail("Read called AI"))
    assert match.latest_assessment(tmp_path, JOB) is None
    assert not list(tmp_path.iterdir())


def test_explicit_review_uses_saved_facts_and_selected_provider_once(setup):
    root, profile_path, calls = setup
    before = profile_path.read_bytes()
    package = match.assess_job(root, JOB, SETTINGS)
    assert len(calls) == 2
    assert calls[0]["settings"]["provider"] == "ollama"
    assert calls[0]["settings"]["ollama_thinking"] is False
    assert calls[1]["schema_type"] is match.JobMatchAssessment
    assert calls[1]["use_web_search"] is False
    assert "reasoning_effort" not in calls[1]  # Let the selected provider settings control it.
    prompt = json.loads(calls[1]["user"])
    assert prompt["profile_evidence"]["experiences/0/bullets/0"]["bullet"]["id"] == "coordinated"
    assert prompt["never_claim"] == PROFILE["never_claim"]
    assert "private@example.org" not in calls[1]["user"]
    assert "private-phone" not in calls[1]["user"]
    assert "private-photo" not in calls[1]["user"]
    assert "Personal tracking note" not in calls[1]["user"]
    assert "never_claim" not in prompt["profile_evidence"]
    assert package["stale"] is False
    assert package["provider"]["ollama_thinking"] is False
    assert package["assessment"]["category"] == "plausible"
    assert profile_path.read_bytes() == before
    assert not (root / "applications").exists()
    assert not (root / "data" / "jobs.db").exists()
    assert "should-not-be-saved" not in (match._store_path(root, JOB)).read_text(encoding="utf-8")
    assert "Required: coordinate experiments." in match.format_assessment(package)
    assert "Saved profile evidence used" in match.format_assessment(package)


def test_codex_ultra_selection_is_not_changed(setup):
    root, _, calls = setup
    settings = {"provider": "codex", "model": "custom-model", "custom_model": True, "reasoning_effort": "ultra"}
    package = match.assess_job(root, JOB, settings)
    assert calls[0]["settings"]["reasoning_effort"] == "ultra"
    assert package["provider"]["reasoning_effort"] == "ultra"
    assert package["provider"]["model"] == "custom-model"


def test_read_flags_changed_job_and_profile_but_not_tracking(setup, monkeypatch):
    root, path, _ = setup
    match.assess_job(root, JOB, SETTINGS)
    monkeypatch.setattr(match, "resolve_response_fn", lambda *_: pytest.fail("Read called AI"))
    assert match.latest_assessment(root, {**JOB, "status": "applied", "notes": "new note"})["stale"] is False
    changed = {**JOB, "description": JOB["description"] + " Extra experience required."}
    assert match.latest_assessment(root, changed)["stale"] is True
    profile = deepcopy(PROFILE)
    profile["contact"]["phone"] = "different"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    assert match.latest_assessment(root, JOB)["stale"] is False
    profile["skills"][0]["text"] = "Intermediate Python"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    package = match.latest_assessment(root, JOB)
    assert package["stale"] is True
    assert "OUTDATED" in match.format_assessment(package)


def test_profile_change_while_ai_works_is_marked_stale(setup, monkeypatch):
    root, path, _ = setup
    def respond(**kwargs):
        profile = deepcopy(PROFILE)
        profile["candidate_summary"] = "Changed during review"
        path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        return response(), {}
    monkeypatch.setattr(match, "resolve_response_fn", lambda _: respond)
    assert match.assess_job(root, JOB, SETTINGS)["stale"] is True


@pytest.mark.parametrize("problem", ["unknown_evidence", "uncited_support", "invented_requirement", "empty_requirements", "wrong_schema"])
def test_invalid_ai_output_preserves_previous_review(setup, monkeypatch, problem):
    root, _, _ = setup
    match.assess_job(root, JOB, SETTINGS)
    path = match._store_path(root, JOB)
    before = path.read_bytes()
    answer = response()
    if problem == "unknown_evidence":
        answer["strengths"][0]["evidence_ids"] = ["invented"]
    elif problem == "uncited_support":
        answer["requirements"][0]["evidence_ids"] = []
    elif problem == "invented_requirement":
        answer["requirements"][0]["job_quote"] = "Ten years of sales experience"
    elif problem == "empty_requirements":
        answer["requirements"] = []
    else:
        answer["match_percentage"] = 99
    monkeypatch.setattr(match, "resolve_response_fn", lambda _: lambda **kwargs: (answer, {}))
    with pytest.raises(ValueError):
        match.assess_job(root, JOB, SETTINGS)
    assert path.read_bytes() == before


def test_missing_evidence_is_not_a_definite_absence(setup, monkeypatch):
    root, _, _ = setup
    answer = response()
    answer["requirements"][0].update(assessment="not_documented", evidence_ids=[], explanation="Please add relevant evidence if available.")
    monkeypatch.setattr(match, "resolve_response_fn", lambda _: lambda **kwargs: (answer, {}))
    package = match.assess_job(root, JOB, SETTINGS)
    text = match.format_assessment(package)
    assert "not documented" in text
    assert "not proof that you lack it" in text
    assert "not_documented, not that the candidate definitely lacks" in match.SYSTEM_PROMPT


def test_per_job_isolation_and_whitespace_in_quotes(setup, monkeypatch):
    root, _, _ = setup
    answer = response()
    answer["requirements"][0]["job_quote"] = "Required:  coordinate\nexperiments."
    monkeypatch.setattr(match, "resolve_response_fn", lambda _: lambda **kwargs: (answer, {}))
    match.assess_job(root, JOB, SETTINGS)
    assert match.latest_assessment(root, {**JOB, "job_id": "job_aaaaaaaaaaaa"}) is None


@pytest.mark.parametrize("job_id", ["../../outside", "", None, "job_nope"])
def test_invalid_job_id_never_calls_provider(setup, job_id):
    root, _, calls = setup
    with pytest.raises(ValueError):
        match.assess_job(root, {**JOB, "job_id": job_id}, SETTINGS)
    assert not calls
    assert not (root / "data" / "job_assessments").exists()


def test_missing_description_or_profile_never_calls_provider(setup):
    root, path, calls = setup
    with pytest.raises(ValueError, match="job description"):
        match.assess_job(root, {**JOB, "description": ""}, SETTINGS)
    path.unlink()
    with pytest.raises(ValueError, match="Save your profile"):
        match.assess_job(root, JOB, SETTINGS)
    assert not calls


def test_invalid_saved_review_is_reported_without_overwrite(setup):
    root, _, calls = setup
    match.assess_job(root, JOB, SETTINGS)
    path = match._store_path(root, JOB)
    package = json.loads(path.read_text(encoding="utf-8"))
    package["assessment"]["strengths"][0]["evidence_ids"] = ["invented"]
    path.write_text(json.dumps(package), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="could not be read"):
        match.latest_assessment(root, JOB)
    assert len(calls) == 2
    assert path.read_bytes() == before
