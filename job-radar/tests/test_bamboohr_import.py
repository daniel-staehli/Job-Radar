"""Synthetic BambooHR fixtures: no downloaded vacancy or candidate data."""
import json

import pytest
import yaml

from job_radar.url_import import FetchError, _ats_endpoint, prepare_job_url


URL = "https://example-bio.bamboohr.com/careers/123"
DETAIL_URL = URL + "/detail"


def shell(branding=None):
    if branding is None:
        branding = {"site": {"logo": {"alt": "Example Bio"}}}
    return '<html><script id="poData" type="application/json">' + json.dumps(branding) + '</script><div id="root"></div></html>'


def detail(**changes):
    opening = {
        "jobOpeningShareUrl": URL,
        "jobOpeningName": "Application Scientist",
        "jobOpeningStatus": "Open",
        "location": {"city": "Lausanne", "state": "Vaud", "addressCountry": "Switzerland"},
        "description": "<p>Support research.</p><ul><li>Explain experiments.</li></ul>",
        "employmentStatusLabel": "Full-Time",
        "datePosted": "2026-09-20",
    }
    opening.update(changes)
    return {"meta": {}, "result": {"jobOpening": opening, "formFields": {}}}


def importer(tmp_path, *, url=URL, page_text=None, payload=None, dry_run=True, api_final=None):
    calls = []
    if page_text is None:
        page_text = shell()
    if payload is None:
        payload = detail()
    raw = payload if isinstance(payload, str) else json.dumps(payload)

    def fetch(requested):
        calls.append(requested)
        if requested == url:
            return page_text, requested
        assert requested == DETAIL_URL
        return raw, api_final or requested

    result = prepare_job_url(url, output_dir=tmp_path, dry_run=dry_run, fetch_fn=fetch)
    return result, calls


def test_bamboohr_shell_and_detail_produce_complete_literal_draft_and_audit(tmp_path):
    result, calls = importer(tmp_path, dry_run=False)
    assert calls == [URL, DETAIL_URL]
    assert result["complete"] and result["needs_review"]
    assert result["extraction_method"] == "bamboohr-public-api"
    job = result["job"]
    assert job["source_url"] == URL
    assert job["company"] == "Example Bio"
    assert job["title"] == "Application Scientist"
    assert job["location"] == "Lausanne, Vaud, Switzerland"
    assert job["country"] == "Switzerland"
    assert job["description"] == "Support research.\n\nExplain experiments."
    assert job["employment_type"] == "Full-Time"
    assert job["posted_date"] == "2026-09-20"
    assert yaml.safe_load(result["yaml_path"].read_text(encoding="utf-8")) == job
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["model_used"] is False
    assert audit["needs_review"] is True
    assert [source["url"] for source in audit["sources"]] == calls
    assert all(len(source["sha256"]) == 64 for source in audit["sources"])
    assert not any("Paste the full vacancy" in warning for warning in result["warnings"])


@pytest.mark.parametrize("url", [URL + "/", URL + "?source=referral", URL.replace("example-bio", "EXAMPLE-BIO")])
def test_bamboohr_url_variants_keep_original_identity(tmp_path, url):
    result, calls = importer(tmp_path, url=url)
    assert result["complete"]
    assert result["job"]["source_url"] == url
    assert calls == [url, DETAIL_URL]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("share_url", [
    "https://example-bio.bamboohr.com/careers/124",
    "https://other-employer.bamboohr.com/careers/123",
    "https://example-bio.bamboohr.com.evil.example/careers/123",
    "http://127.0.0.1/careers/123", "file:///careers/123", "", None,
])
def test_bamboohr_rejects_missing_or_mismatched_opening_identity(tmp_path, share_url):
    result, _ = importer(tmp_path, payload=detail(jobOpeningShareUrl=share_url))
    assert not result["complete"]
    assert result["job"]["title"] == ""
    assert result["job"]["description"] == ""
    assert result["job"]["source_url"] == URL
    assert result["warnings"]


@pytest.mark.parametrize("url", [
    "https://bamboohr.com/careers/123",
    "https://example-bio.notbamboohr.com/careers/123",
    "https://example-bio.bamboohr.com.evil.example/careers/123",
    "https://nested.example-bio.bamboohr.com/careers/123",
    "https://example-bio.bamboohr.com/careers/scientist",
    "https://example-bio.bamboohr.com/jobs/123",
    "https://example-bio.bamboohr.com/careers/123/detail",
    "https://example-bio.bamboohr.com/careers/123/extra",
])
def test_bamboohr_adapter_matches_only_direct_tenant_vacancy_links(url):
    assert _ats_endpoint(url) is None


@pytest.mark.parametrize("status", ["Closed", "Draft", "Archived"])
def test_bamboohr_closed_or_unpublished_openings_are_not_imported(tmp_path, status):
    result, _ = importer(tmp_path, payload=detail(jobOpeningStatus=status))
    assert not result["complete"]
    assert result["job"]["title"] == ""
    assert result["job"]["description"] == ""


@pytest.mark.parametrize("raw", ["{broken", "null", "[]", '{"result": null}', '{"result": []}', '{"result": {"jobOpening": []}}'])
def test_bamboohr_malformed_feed_returns_reviewable_draft(tmp_path, raw):
    result, _ = importer(tmp_path, payload=raw)
    assert not result["complete"]
    assert result["job"]["source_url"] == URL
    assert result["job"]["title"] == ""
    assert result["warnings"]


def test_bamboohr_missing_branding_never_guesses_company_from_tenant(tmp_path):
    result, _ = importer(tmp_path, page_text="<div id='root'></div>")
    assert result["job"]["company"] == ""
    assert result["job"]["title"] == "Application Scientist"
    assert result["missing_fields"] == ["company"]


@pytest.mark.parametrize("branding", [[], {"site": None}, {"site": {"logo": None}}, {"site": {"logo": {"alt": []}}}])
def test_bamboohr_malformed_branding_does_not_discard_valid_detail(tmp_path, branding):
    result, _ = importer(tmp_path, page_text=shell(branding))
    assert result["job"]["company"] == ""
    assert result["job"]["title"] == "Application Scientist"


def test_bamboohr_invalid_json_branding_does_not_discard_valid_detail(tmp_path):
    html = '<script id="poData" type="application/json">{broken}</script><div id="root"></div>'
    result, _ = importer(tmp_path, page_text=html)
    assert result["job"]["company"] == ""
    assert result["job"]["title"] == "Application Scientist"


def test_bamboohr_branding_is_not_general_employer_inference(tmp_path):
    url = "https://careers.example.com/jobs/123"
    result = prepare_job_url(url, output_dir=tmp_path, dry_run=True, fetch_fn=lambda requested: (shell(), requested))
    assert result["job"]["company"] == ""


@pytest.mark.parametrize("location", [None, [], {"city": None, "state": [], "addressCountry": {}}])
def test_bamboohr_null_and_malformed_optional_fields_remain_blank(tmp_path, location):
    result, _ = importer(tmp_path, payload=detail(location=location, employmentStatusLabel=None, datePosted=None))
    assert result["job"]["title"] == "Application Scientist"
    assert result["job"]["location"] == ""
    assert result["missing_fields"] == ["location"]
    assert not result["job"].get("employment_type")
    assert not result["job"].get("posted_date")


def test_bamboohr_description_is_decoded_without_executable_or_hidden_text(tmp_path):
    result, _ = importer(tmp_path, payload=detail(description='&lt;p&gt;Research &amp;amp; development.&lt;/p&gt;<script>execute()</script><p hidden>hidden text</p>'))
    assert result["job"]["description"] == "Research & development."


def test_bamboohr_detail_failure_preserves_reviewable_page_and_url(tmp_path):
    calls = []
    def fetch(requested):
        calls.append(requested)
        if requested == URL:
            return shell(), requested
        assert requested == DETAIL_URL
        raise FetchError("Website returned HTTP 404.")
    result = prepare_job_url(URL, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert calls == [URL, DETAIL_URL]
    assert not result["complete"]
    assert result["job"]["source_url"] == URL
    assert any("404" in warning for warning in result["warnings"])


def test_bamboohr_complete_jsonld_does_not_fetch_detail(tmp_path):
    posting = {
        "@type": "JobPosting", "title": "Scientist",
        "hiringOrganization": {"name": "Example Bio"},
        "jobLocation": {"address": {"addressLocality": "Basel", "addressCountry": "Switzerland"}},
        "description": "Research proteins.",
    }
    html = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
    calls = []
    def fetch(requested):
        calls.append(requested)
        assert requested == URL
        return html, requested
    result = prepare_job_url(URL, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert result["complete"]
    assert result["extraction_method"] == "json-ld"
    assert calls == [URL]


def test_bamboohr_private_final_feed_url_is_rejected_before_parsing(tmp_path):
    result, _ = importer(tmp_path, api_final="http://127.0.0.1/private")
    assert not result["complete"]
    assert result["job"]["title"] == ""
    assert any("private" in warning for warning in result["warnings"])
