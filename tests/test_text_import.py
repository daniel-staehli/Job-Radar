import hashlib
import json
import socket

import pytest
import yaml

from job_radar import text_import
from job_radar.text_import import prepare_job_text


URL = "https://careers.example.com/jobs/scientist"
TEXT = "Company: Example Bio\nJob title: Scientist\nLocation: Lausanne\n\nWe study proteins.\n• Plan experiments.\n• Coordinate samples."


def test_local_draft_preserves_description_and_has_audit(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Text import must not use networking")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = prepare_job_text(TEXT, output_dir=tmp_path, source_url=URL)
    assert result["job"] == {"company": "Example Bio", "title": "Scientist", "location": "Lausanne",
                             "source_url": URL, "description": TEXT, "source": "pasted text"}
    assert result["complete"] is True
    assert result["needs_review"] is True
    assert result["extraction_method"] == "pasted-text-labels"
    assert result["missing_fields"] == []
    assert yaml.safe_load(result["yaml_path"].read_text(encoding="utf-8")) == result["job"]
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["model_used"] is False
    assert audit["network_used"] is False
    assert audit["source_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert audit["needs_review"] is True
    assert set(item.name for item in tmp_path.iterdir()) == {result["yaml_path"].name, result["audit_path"].name}


def test_source_url_is_optional(tmp_path):
    result = prepare_job_text(TEXT, output_dir=tmp_path)
    assert result["complete"] is True
    assert result["job"]["source_url"] == ""
    assert "source_url" not in result["missing_fields"]


@pytest.mark.parametrize("text, company, title, location", [
    ("Arbeitgeber: Muster AG\nStellenbezeichnung: Forscher\nArbeitsort: Zürich", "Muster AG", "Forscher", "Zürich"),
    ("Entreprise : Exemple\nIntitulé du poste : Scientifique\nLieu de travail : Genève", "Exemple", "Scientifique", "Genève"),
    ("\tEMPLOYER\u00a0:\u00a0Example Bio\r\n- Position: Scientist\r\n• Location: Remote", "Example Bio", "Scientist", "Remote"),
    ("Firma = Muster AG\nBerufsbezeichnung = Forscher\nStandort = Basel", "Muster AG", "Forscher", "Basel"),
])
def test_explicit_multilingual_labels(tmp_path, text, company, title, location):
    job = prepare_job_text(text, output_dir=tmp_path)["job"]
    assert (job["company"], job["title"], job["location"]) == (company, title, location)


def test_unlabelled_prose_and_email_do_not_invent_fields(tmp_path):
    text = "Scientist\nExample Bio is hiring in Lausanne.\nApply at hiring@example.com.\nhttps://example.com/about"
    result = prepare_job_text(text, output_dir=tmp_path)
    assert result["job"]["description"] == text
    assert result["job"]["company"] == result["job"]["title"] == result["job"]["location"] == ""
    assert result["job"]["source_url"] == ""
    assert result["missing_fields"] == ["company", "title", "location"]
    assert result["extraction_method"] == "pasted-text"


def test_conflicting_values_stay_blank_but_duplicate_labels_are_allowed(tmp_path):
    text = TEXT + "\nEmployer: Other Company\nTitle: Researcher\nWork location: Basel"
    result = prepare_job_text(text, output_dir=tmp_path)
    assert result["missing_fields"] == ["company", "title", "location"]
    assert len([item for item in result["warnings"] if "Several different" in item]) == 3
    duplicate = prepare_job_text(TEXT + "\nEmployer: example bio\nTitle: Scientist", output_dir=tmp_path)
    assert duplicate["job"]["company"] == "Example Bio"
    assert duplicate["job"]["title"] == "Scientist"


@pytest.mark.parametrize("suffix, expected", [
    ("\nJob URL: https://example.com/opening?id=123", "https://example.com/opening?id=123"),
    ("\nLien de l’offre: https://example.com/offre/123", "https://example.com/offre/123"),
    ("\nMore details: " + URL, URL),
    ("\n" + URL + "\n" + URL, URL),
    ("\nhttps://jobs.ashbyhq.com/example/id-123", "https://jobs.ashbyhq.com/example/id-123"),
    ("\nhttps://example.com/careers", ""),
    ("\nhttps://jobs.ashbyhq.com/example", ""),
    ("\nCompany website: https://example.com/about", ""),
    ("\nhttps://example.com/jobs/123\nhttps://example.com/jobs/456", ""),
    ("\nJob URL: https://example.com/jobs/A\nJob URL: https://example.com/jobs/a", ""),
])
def test_url_extraction_is_conservative(tmp_path, suffix, expected):
    result = prepare_job_text(TEXT + suffix, output_dir=tmp_path)
    assert result["job"]["source_url"] == expected


def test_explicit_source_link_wins_with_review_warning(tmp_path):
    result = prepare_job_text(TEXT + "\nJob URL: https://example.com/jobs/other", output_dir=tmp_path, source_url=URL)
    assert result["job"]["source_url"] == URL
    assert any("supplied source link differs" in item for item in result["warnings"])


@pytest.mark.parametrize("source", ["http://localhost/a", "http://127.0.0.1/a", "https://user:pass@example.com/a", "file:///C:/notes", "https://example.com/path with spaces", None])
def test_invalid_supplied_links_rejected_before_files_written(tmp_path, source):
    with pytest.raises(ValueError):
        prepare_job_text(TEXT, output_dir=tmp_path, source_url=source)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("link", ["http://localhost:11434/v1", "http://127.0.0.1/jobs/1", "https://user:pass@example.com/jobs/1", "not a link"])
def test_invalid_labelled_links_are_not_selected_or_fetched(tmp_path, link):
    result = prepare_job_text(TEXT + "\nURL: " + link, output_dir=tmp_path)
    assert result["job"]["source_url"] == ""
    assert any("invalid or ambiguous" in item for item in result["warnings"])


@pytest.mark.parametrize("value", [None, 123, [], b"text", "", " \t\n", "ab\x00cd", "ab\x7fcd", "ab\x85cd", "ab\ud800cd"])
def test_invalid_input_rejected_without_output(tmp_path, value):
    with pytest.raises(ValueError):
        prepare_job_text(value, output_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_size_limit_counts_utf8_bytes(tmp_path):
    with pytest.raises(ValueError, match="250 KB"):
        prepare_job_text("é" * (text_import.MAX_TEXT_BYTES // 2 + 1), output_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_generated_filenames_are_bounded_and_never_use_label_paths(tmp_path):
    text = "Company: ../../" + "X" * 300 + "\nTitle: Scientist\nLocation: Basel"
    first = prepare_job_text(text, output_dir=tmp_path)
    second = prepare_job_text(text, output_dir=tmp_path)
    assert first["yaml_path"].parent == second["yaml_path"].parent == tmp_path
    assert first["yaml_path"] != second["yaml_path"]
    assert len(first["yaml_path"].name) < 50


def test_newline_normalisation_preserves_all_prose(tmp_path):
    result = prepare_job_text("  First\r\nSecond\rThird\u2028Fourth\u2029Fifth  ", output_dir=tmp_path)
    assert result["job"]["description"] == "First\nSecond\nThird\nFourth\nFifth"
