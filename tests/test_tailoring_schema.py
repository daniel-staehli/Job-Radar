"""Profile-bound generation checks use fictional data and mocked Ollama only."""
import copy
import json

import httpx
from pydantic import ValidationError
import pytest
import yaml

from job_radar import cv, providers
from job_radar.config import ROOT
from job_radar.db import JobDatabase
from job_radar.models import TailoredApplication
from job_radar.tailoring_schema import application_schema
from test_cv import job
from test_revisions import application, profile


def populated_profile():
    value = profile()
    value["experiences"].append({
        "key": "teaching", "title": "Teaching Assistant", "organisation": "Test College",
        "location": "Test City", "dates": "2019-2020", "subtitle": "Teaching laboratory",
        "bullets": [{"id": "teaching_support", "text": "Supported practical classes."}],
    })
    value["skills"] = [{"id": "skill_planning", "text": "Experiment planning"}]
    value["publications"] = [{"id": "pub_methods", "text": "Example methods publication"}]
    value["additional_experience"] = [{"id": "add_workshop", "text": "Example workshop"}]
    return value


def populated_application():
    value = application().model_dump()
    value["selected_roles"].append({"role_key": "teaching", "bullet_ids": ["teaching_support"]})
    value["selected_skill_ids"] = ["skill_planning"]
    value["selected_publication_ids"] = ["pub_methods"]
    value["selected_additional_ids"] = ["add_workshop"]
    return value


def test_tailoring_schema_accepts_grounded_selection_and_stays_base_compatible():
    selected = populated_application()
    parsed = application_schema(populated_profile()).model_validate(selected)
    assert isinstance(parsed, TailoredApplication)
    assert parsed.model_dump() == selected
    assert cv.validate_tailoring(parsed, populated_profile()) == []


def test_tailoring_schema_rejects_target_job_title_as_past_experience_key():
    selected = populated_application()
    selected["selected_roles"][0]["role_key"] = "medical_science_liaison_oncology"
    # The chosen bullets are valid; their invented parent is still rejected.
    with pytest.raises(ValidationError):
        application_schema(populated_profile()).model_validate(selected)


def test_tailoring_schema_rejects_bullet_from_another_verified_role():
    selected = populated_application()
    selected["selected_roles"][0]["bullet_ids"] = ["research_plan", "teaching_support"]
    with pytest.raises(ValidationError):
        application_schema(populated_profile()).model_validate(selected)


def test_tailoring_schema_rejects_invented_bullet_id():
    selected = populated_application()
    selected["selected_roles"][0]["bullet_ids"] = ["invented_claim"]
    with pytest.raises(ValidationError):
        application_schema(populated_profile()).model_validate(selected)


@pytest.mark.parametrize("field", [
    "headline_tags", "selected_skill_ids", "selected_publication_ids", "selected_additional_ids",
])
def test_tailoring_schema_rejects_unknown_selection_ids(field):
    selected = populated_application()
    selected[field] = ["not_in_verified_profile"]
    with pytest.raises(ValidationError):
        application_schema(populated_profile()).model_validate(selected)


@pytest.mark.parametrize("source,selection", [
    ("skills", "selected_skill_ids"),
    ("publications", "selected_publication_ids"),
    ("additional_experience", "selected_additional_ids"),
    ("allowed_headline_tags", "headline_tags"),
])
def test_tailoring_schema_empty_source_collection_allows_only_empty_selection(source, selection):
    candidate = populated_profile()
    candidate[source] = []
    proposed = populated_application()
    proposed[selection] = []
    schema = application_schema(candidate)
    schema.model_validate(proposed)
    proposed[selection] = ["made_up"]
    with pytest.raises(ValidationError):
        schema.model_validate(proposed)


def test_tailoring_schema_empty_experience_collection_allows_no_selected_role():
    candidate = populated_profile()
    candidate["experiences"] = []
    proposed = populated_application()
    proposed["selected_roles"] = []
    schema = application_schema(candidate)
    schema.model_validate(proposed)
    proposed["selected_roles"] = [{"role_key": "research", "bullet_ids": []}]
    with pytest.raises(ValidationError):
        schema.model_validate(proposed)


def test_tailoring_schema_role_without_bullets_cannot_gain_invented_claims():
    candidate = profile()
    candidate["experiences"][0]["bullets"] = []
    proposed = application().model_dump()
    proposed["selected_roles"][0]["bullet_ids"] = []
    schema = application_schema(candidate)
    schema.model_validate(proposed)
    proposed["selected_roles"][0]["bullet_ids"] = ["research_plan"]
    with pytest.raises(ValidationError):
        schema.model_validate(proposed)


def test_tailoring_schema_does_not_change_profile_and_is_stable_for_resume():
    candidate = populated_profile()
    snapshot = copy.deepcopy(candidate)
    first = application_schema(candidate).model_json_schema()
    second = application_schema(candidate).model_json_schema()
    assert first == second
    assert candidate == snapshot


def generation_arguments(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, _ = db.upsert_job(job())
    candidate_path = tmp_path / "profile.yaml"
    candidate = profile()
    candidate["experiences"][0]["role"] = "Researcher at Test Institute"
    candidate_path.write_text(yaml.safe_dump(candidate), encoding="utf-8")
    return {
        "db": db, "job_ids_override": [job_id], "selection_path": tmp_path / "unused.yaml",
        "profile_path": candidate_path, "template_path": ROOT / "templates" / "cv_default.tex.j2",
        "output_root": tmp_path / "applications", "dry_run": False, "compile_pdf": False,
        "offline": True,
        "provider_settings": {**providers.default_settings("ollama"), "model": "synthetic-local-model"},
    }


def test_tailoring_schema_is_used_in_generation_preview_and_ollama_request(tmp_path, monkeypatch):
    arguments = generation_arguments(tmp_path)
    payloads, schemas = [], []

    def handler(request):
        assert request.url.path == "/api/chat"
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "synthetic-local-model", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": application().model_dump_json()},
        })

    resolve = providers.resolve_response_fn

    def resolver(settings):
        provider = resolve(settings, transport=httpx.MockTransport(handler))

        def respond(**kwargs):
            schemas.append(kwargs["schema_type"])
            return provider(**kwargs)

        return respond

    monkeypatch.setattr(cv, "resolve_response_fn", resolver)
    outputs = cv.generate_cvs(**arguments)
    assert len(outputs) == len(payloads) == len(schemas) == 1
    directory = outputs[0]
    preview = json.loads((directory / "prompt.json").read_text(encoding="utf-8"))
    bound = schemas[0]
    assert preview["schema"] == bound.model_json_schema()
    assert payloads[0]["format"] == providers._schema(bound, "ollama")
    invalid = application().model_dump()
    invalid["selected_roles"][0]["role_key"] = "target_job_title"
    with pytest.raises(ValidationError):
        bound.model_validate(invalid)
    assert (directory / "cv.tex").is_file()
    assert (directory / "motivation_letter.tex").is_file()
    assert not (directory / "VALIDATION_FAILED.txt").exists()


def test_tailoring_schema_base_model_mock_still_receives_semantic_validation(tmp_path):
    arguments = generation_arguments(tmp_path)
    unsupported = application().model_copy(deep=True)
    unsupported.selected_roles[0].role_key = "target_job_title"
    calls = []

    def respond(**request):
        calls.append(request)
        # A custom backend can return the ordinary base class. It must not bypass validation.
        return unsupported, {"mock": True}

    outputs = cv.generate_cvs(**arguments, response_fn=respond)
    assert len(calls) == len(outputs) == 1
    failure = (outputs[0] / "VALIDATION_FAILED.txt").read_text(encoding="utf-8")
    assert "Unknown role key: target_job_title" in failure
    assert not (outputs[0] / "cv.tex").exists()
    assert not (outputs[0] / "motivation_letter.tex").exists()

