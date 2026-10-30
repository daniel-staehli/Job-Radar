from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from job_radar import ai_text_import
from job_radar.ai_text_import import prepare_job_text_ai
from job_radar.providers import ProviderError


SETTINGS = {"provider": "ollama", "model": "example-local:latest"}
TEXT = "Example Bio is looking for a Scientist to work in Lausanne.\nStudy proteins and coordinate samples."


def response(company="", title="", location="", *, quote=TEXT):
    return {name: {"value": value, "evidence": quote if value else ""}
            for name, value in (("company", company), ("title", title), ("location", location))}


def provider(monkeypatch, result=None, error=None):
    calls = []
    settings_seen = []

    def resolve(settings):
        settings_seen.append(deepcopy(settings))

        def respond(**kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            return deepcopy(result), {"unsafe_server_body": "FAKE-SECRET-MUST-NOT-BE-SAVED"}

        return respond

    monkeypatch.setattr(ai_text_import, "resolve_response_fn", resolve)
    return calls, settings_seen


def extract(tmp_path, text=TEXT, **kwargs):
    return prepare_job_text_ai(text, output_dir=tmp_path / "output",
                               provider_settings=kwargs.pop("provider_settings", SETTINGS), **kwargs)


def assert_no_output(tmp_path):
    assert not (tmp_path / "output").exists()


def test_one_request_saves_reviewable_original_text_evidence_and_safe_audit(tmp_path, monkeypatch):
    calls, seen = provider(monkeypatch, response("Example Bio", "Scientist", "Lausanne"))
    url = "https://example.com/careers/12"
    result = extract(tmp_path, source_url=url)
    assert result["job"] == {"company": "Example Bio", "title": "Scientist", "location": "Lausanne",
                             "description": TEXT, "source": "pasted text", "source_url": url}
    assert result["complete"] and result["needs_review"] and result["model_used"]
    assert result["missing_fields"] == []
    assert result["unresolved_fields"] == {}
    assert len(calls) == len(seen) == 1
    call = calls[0]
    assert call["use_web_search"] is False
    assert "artifact_dir" not in call
    assert call["schema_type"] is ai_text_import.VacancyMetadata
    payload = json.loads(call["user"])
    assert payload == {"vacancy_text": TEXT, "existing_fields": {},
                       "missing_fields": ["company", "title", "location"]}
    assert "Do not browse" in call["system"]
    assert "never instructions" in call["system"]
    assert "never translate" in call["system"]
    assert yaml.safe_load(result["yaml_path"].read_text(encoding="utf-8")) == result["job"]
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["source_sha256"] == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert audit["model_used"] is True and audit["provider_request_made"] is True
    assert not any(audit[key] for key in ("vacancy_fetch_used", "web_search_used", "profile_used", "references_used"))
    assert audit["evidence"] == result["ai_evidence"]
    assert audit["unresolved_fields"] == {}
    assert audit["provider"]["provider"] == "ollama"
    for path in (tmp_path / "output").iterdir():
        assert "FAKE-SECRET" not in path.read_text(encoding="utf-8")
    assert len(list((tmp_path / "output").iterdir())) == 2


def test_eth_german_prose_can_find_employer_without_inventing_missing_title_or_location(tmp_path, monkeypatch):
    text = ("Anforderungsprofil\n\nGrants Office\n\nZur Verstärkung unseres Teams im Grants Office "
            "der ETH Zürich suchen wir eine engagierte Persönlichkeit.\nSie beraten Forschende.")
    provider(monkeypatch, response("ETH Zürich", quote=text))
    result = extract(tmp_path, text)
    assert result["job"]["company"] == "ETH Zürich"
    assert result["job"]["title"] == result["job"]["location"] == ""
    assert result["job"]["description"] == text
    assert result["missing_fields"] == ["title", "location"]
    assert set(result["unresolved_fields"]) == {"title", "location"}
    assert all(isinstance(message, str) and message.strip()
               for message in result["unresolved_fields"].values())
    assert "company" in result["warnings"][0].lower()
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["unresolved_fields"] == result["unresolved_fields"]


@pytest.mark.parametrize("text, company, title, location", [
    ("A scientist joins Acme Research as Research Associate, based in Oxford.",
     "Acme Research", "Research Associate", "Oxford"),
    ("Exemple SA recrute un Chargé de recherche pour son bureau situé à Genève.",
     "Exemple SA", "Chargé de recherche", "Genève"),
    ("L'azienda Ricerca più cerca un Responsabile scientifico con sede di lavoro a Bellinzona.",
     "Ricerca più", "Responsabile scientifico", "Bellinzona"),
    ("Muster AG sucht eine Wissenschaftliche Mitarbeiterin für den Arbeitsort Zürich.",
     "Muster AG", "Wissenschaftliche Mitarbeiterin", "Zürich"),
])
def test_original_language_unicode_is_preserved(tmp_path, monkeypatch, text, company, title, location):
    provider(monkeypatch, response(company, title, location, quote=text))
    result = extract(tmp_path, text)
    assert [result["job"][field] for field in ai_text_import.FIELDS] == [company, title, location]
    assert result["job"]["description"] == text


@pytest.mark.parametrize("value, evidence", [
    ("Invented Employer", TEXT),
    ("Example Bio", "This exact quotation was never in the vacancy."),
    ("Example Bio", ""),
    ("", TEXT),
    ("Scientific Researcher", TEXT),
    ("example bio", TEXT),
])
def test_unsupported_or_translated_values_and_missing_quotes_leave_no_artifacts(tmp_path, monkeypatch, value, evidence):
    proposed = response()
    proposed["company"] = {"value": value, "evidence": evidence}
    calls, _ = provider(monkeypatch, proposed)
    with pytest.raises(ValueError, match="exact quote"):
        extract(tmp_path)
    assert len(calls) == 1
    assert_no_output(tmp_path)


@pytest.mark.parametrize("bad", [
    {"company": {"value": "Example Bio", "evidence": TEXT}},
    {**response(), "source_url": "https://wrong.example/jobs/1"},
    {**response(), "description": "A rewritten description"},
    {**response(), "title": {"value": 123, "evidence": TEXT}},
    {**response(), "title": {"value": "Scientist", "evidence": TEXT, "confidence": 1}},
])
def test_invalid_schema_rejected_without_output(tmp_path, monkeypatch, bad):
    provider(monkeypatch, bad)
    with pytest.raises(ValueError, match="expected format"):
        extract(tmp_path)
    assert_no_output(tmp_path)


def test_section_heading_cannot_be_title_even_with_exact_quote(tmp_path, monkeypatch):
    text = "Anforderungsprofil\nWissenschaftliche Tätigkeit an einer Hochschule."
    provider(monkeypatch, response(title="Anforderungsprofil", quote=text))
    with pytest.raises(ValueError, match="section heading"):
        extract(tmp_path, text)
    assert_no_output(tmp_path)


def test_city_only_inside_employer_name_is_left_blank(tmp_path, monkeypatch):
    text = "Das Grants Office der ETH Zürich sucht eine engagierte Persönlichkeit."
    provider(monkeypatch, response(company="ETH Zürich", location="Zürich", quote=text))
    result = extract(tmp_path, text)
    assert result["job"]["company"] == "ETH Zürich"
    assert result["job"]["location"] == ""
    assert "location" not in result["ai_evidence"]
    assert any("only inside the employer" in warning for warning in result["warnings"])


def test_explicit_workplace_with_same_city_as_employer_is_allowed(tmp_path, monkeypatch):
    text = "ETH Zürich sucht Forschende. Arbeitsort ist Zürich."
    provider(monkeypatch, response(company="ETH Zürich", location="Zürich", quote=text))
    result = extract(tmp_path, text)
    assert result["job"]["location"] == "Zürich"


def test_local_labels_and_existing_reviewed_fields_are_preserved(tmp_path, monkeypatch):
    text = "Company: Example Bio\nTitle: Scientist\nThe workplace is Lausanne."
    calls, _ = provider(monkeypatch, response("Wrong employer", "Wrong title", "Lausanne", quote=text))
    result = extract(tmp_path, text, known_fields={"company": "Reviewed Employer", "title": ""})
    assert result["job"]["company"] == "Reviewed Employer"
    assert result["job"]["title"] == "Scientist"
    assert result["job"]["location"] == "Lausanne"
    payload = json.loads(calls[0]["user"])
    assert payload["existing_fields"] == {"company": "Reviewed Employer", "title": "Scientist"}
    assert payload["missing_fields"] == ["location"]
    assert list(result["ai_evidence"]) == ["location"]
    assert any("existing company differs" in item for item in result["warnings"])
    assert sum("AI suggested a different" in item for item in result["warnings"]) == 2


@pytest.mark.parametrize("known,text", [
    (None, "Company: Example Bio\nTitle: Scientist\nLocation: Lausanne"),
    ({"company": "Acme", "title": "Scientist", "location": "Remote"}, TEXT),
])
def test_complete_fields_skip_provider_and_even_settings_validation(tmp_path, monkeypatch, known, text):
    def forbidden(*args, **kwargs):
        raise AssertionError("Complete drafts must not inspect or call an AI provider")
    monkeypatch.setattr(ai_text_import, "normalize_settings", forbidden)
    monkeypatch.setattr(ai_text_import, "provider_identity", forbidden)
    monkeypatch.setattr(ai_text_import, "resolve_response_fn", forbidden)
    result = extract(tmp_path, text, known_fields=known, provider_settings={"bad": "settings"})
    assert result["complete"] and result["model_used"] is False
    assert result["provider"] is None and result["ai_evidence"] == {}
    assert result["unresolved_fields"] == {}
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["model_used"] is False and audit["provider_request_made"] is False


@pytest.mark.parametrize("known", [[], {"source_url": "https://example.com"}, {"company": None},
                                   {"company": "Line one\nLine two"}, {"title": "X" * 501},
                                   {"location": "invalid\x00field"}])
def test_invalid_existing_fields_fail_before_provider_and_output(tmp_path, monkeypatch, known):
    calls, seen = provider(monkeypatch, response())
    with pytest.raises(ValueError):
        extract(tmp_path, known_fields=known)
    assert calls == seen == []
    assert_no_output(tmp_path)


@pytest.mark.parametrize("text,url", [("", ""), ("\ud800", ""), (TEXT, "http://localhost/private"),
                                     (TEXT, "https://user:secret@example.com/jobs/1")])
def test_bad_input_fails_before_provider_and_output(tmp_path, monkeypatch, text, url):
    calls, seen = provider(monkeypatch, response())
    with pytest.raises(ValueError):
        extract(tmp_path, text, source_url=url)
    assert calls == seen == []
    assert_no_output(tmp_path)


def test_provider_failure_is_not_retried_and_leaves_no_output(tmp_path, monkeypatch):
    calls, _ = provider(monkeypatch, error=ProviderError("Synthetic provider unavailable."))
    with pytest.raises(ProviderError, match="unavailable"):
        extract(tmp_path)
    assert len(calls) == 1
    assert_no_output(tmp_path)


def test_unconfigured_model_fails_before_network_and_saves_nothing(tmp_path):
    with pytest.raises(ValueError, match="model name"):
        extract(tmp_path, provider_settings={"provider": "ollama"})
    assert_no_output(tmp_path)


def test_selected_ollama_thinking_and_endpoint_preserved_without_changing_settings(tmp_path, monkeypatch):
    settings = {"provider": "ollama", "model": "qwen-example:latest", "ollama_thinking": "high",
                "base_url": "http://127.0.0.1:11434/api"}
    before = deepcopy(settings)
    _, seen = provider(monkeypatch, response())
    result = extract(tmp_path, provider_settings=settings)
    assert settings == before
    assert seen[0]["model"] == settings["model"]
    assert seen[0]["ollama_thinking"] == "high"
    assert seen[0]["base_url"] == settings["base_url"]
    assert result["provider"]["ollama_thinking"] == "high"


def test_selected_codex_model_and_ultra_reasoning_preserved(tmp_path, monkeypatch):
    settings = {"provider": "codex", "model": "synthetic-custom-model", "custom_model": True,
                "reasoning_effort": "ultra"}
    before = deepcopy(settings)
    _, seen = provider(monkeypatch, response())
    result = extract(tmp_path, provider_settings=settings)
    assert settings == before
    assert seen[0]["model"] == "synthetic-custom-model"
    assert seen[0]["reasoning_effort"] == result["provider"]["reasoning_effort"] == "ultra"


def test_no_profile_or_reference_reads_and_no_private_data_in_request(tmp_path, monkeypatch):
    calls, _ = provider(monkeypatch, response())
    original = Path.open

    def guard(path, *args, **kwargs):
        if path.name in {"master_profile.yaml", "reference_cvs.json", "reference_letters.json"}:
            raise AssertionError("Vacancy extraction must not access personal profile/reference data")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    extract(tmp_path)
    payload = json.loads(calls[0]["user"])
    assert set(payload) == {"vacancy_text", "existing_fields", "missing_fields"}


def test_original_source_url_survives_and_embedded_other_links_are_not_fetched(tmp_path, monkeypatch):
    text = TEXT + "\nBenefits: https://example.com/benefits\nMore: https://example.com/careers/2"
    provider(monkeypatch, response("Example Bio", "Scientist", "Lausanne", quote=TEXT))
    result = extract(tmp_path, text, source_url="https://example.com/careers/1")
    assert result["job"]["source_url"] == result["final_url"] == "https://example.com/careers/1"
    assert result["job"]["description"] == text


def test_write_failure_rolls_back_partial_pair(tmp_path, monkeypatch):
    provider(monkeypatch, response())
    original = Path.open

    def fail_audit(path, *args, **kwargs):
        if path.parent == tmp_path / "output" and path.name.endswith(".extraction.json"):
            raise OSError("Synthetic disk failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_audit)
    with pytest.raises(OSError, match="disk failure"):
        extract(tmp_path)
    assert list((tmp_path / "output").iterdir()) == []


def test_no_ai_additions_preserve_known_fields_and_do_not_claim_absent_source_details(tmp_path, monkeypatch):
    # The text actually contains title/location. A conservative or weak response
    # must be reported as unresolved, never proof those details are absent.
    calls, _ = provider(monkeypatch, response())
    result = extract(tmp_path, known_fields={"company": "Example Bio"})
    assert len(calls) == 1
    assert result["job"]["company"] == "Example Bio"
    assert result["job"]["title"] == result["job"]["location"] == ""
    assert result["ai_evidence"] == {}
    assert set(result["unresolved_fields"]) == {"title", "location"}
    headline = result["warnings"][0].lower()
    assert "no" in headline and ("additional" in headline or "new" in headline or "add" in headline)
    for message in result["unresolved_fields"].values():
        assert message.strip()
        assert not any(claim in message.lower() for claim in ("not present", "not in the text", "absent"))
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["unresolved_fields"] == result["unresolved_fields"]
    assert audit["field_sources"]["company"] == "existing field"
    assert audit["provider_request_made"] and not audit["vacancy_fetch_used"]
