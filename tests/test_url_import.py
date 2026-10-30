import json
import socket

import pytest
import yaml

from job_radar import url_import
from job_radar.url_import import FetchError, html_to_text, parse_job_page, prepare_job_url, validate_public_url


URL = "https://careers.example.com/jobs/scientist"


def job_posting(**updates):
    value = {
        "@type": "JobPosting", "title": "Application Scientist",
        "hiringOrganization": {"@type": "Organization", "name": "Example Bio"},
        "jobLocation": {"address": {"addressLocality": "Lausanne", "addressCountry": "Switzerland"}},
        "description": "<p>Support research.</p><ul><li>Explain experiments.</li><li>Train users.</li></ul>",
        "datePosted": "2026-09-20", "validThrough": "2026-10-31", "employmentType": "FULL_TIME",
    }
    value.update(updates)
    return value


def page(data):
    return '<html><script type="application/ld+json">' + json.dumps(data) + "</script></html>"


def test_nested_jsonld_and_referenced_organization_preserve_literal_facts():
    posting = job_posting(hiringOrganization={"@id": "#employer"})
    result = parse_job_page(page({"@graph": [posting, {"@id": "#employer", "@type": "Organization", "name": "Example Bio"}]}), URL)
    assert result["complete"]
    assert result["needs_review"]
    assert result["extraction_method"] == "json-ld"
    assert result["job"]["company"] == "Example Bio"
    assert result["job"]["location"] == "Lausanne, Switzerland"
    assert result["job"]["description"] == "Support research.\n\nExplain experiments.\n\nTrain users."
    assert result["job"]["posted_date"] == "2026-09-20"
    assert result["job"]["deadline"] == "2026-10-31"


def test_job_location_arrays_and_remote_requirements():
    result = parse_job_page(page([job_posting(jobLocation=[
        {"address": {"addressLocality": "Basel", "addressCountry": {"name": "Switzerland"}}},
        {"address": {"addressLocality": "Lausanne", "addressCountry": "Switzerland"}},
    ])]), URL)
    assert result["job"]["location"] == "Basel, Switzerland; Lausanne, Switzerland"
    remote = parse_job_page(page(job_posting(jobLocation=None, jobLocationType="TELECOMMUTE", applicantLocationRequirements={"@type": "Country", "name": "Switzerland"})), URL)
    assert remote["job"]["location"] == "Remote — Switzerland"
    assert remote["job"]["work_mode"] == "remote"


def test_multi_job_page_is_not_silently_first_match():
    first, second = job_posting(url=URL + "-other"), job_posting(title="Second Scientist", url=URL)
    matched = parse_job_page(page({"itemListElement": [first, second]}), URL)
    assert matched["job"]["title"] == "Second Scientist"
    ambiguous = parse_job_page(page([job_posting(), job_posting(title="Another Scientist")]), URL)
    assert not ambiguous["complete"]
    assert ambiguous["extraction_method"] == "ambiguous"
    assert ambiguous["job"]["title"] == ""


def test_fallback_does_not_guess_employer_from_website_or_copy_navigation():
    html = '<html><head><meta property="og:site_name" content="Job Board"></head><body><nav>Menu</nav><h1>Scientist</h1><main><p>Research proteins.</p><script>steal()</script></main><footer>Legal</footer></body></html>'
    result = parse_job_page(html, URL)
    assert result["job"]["title"] == "Scientist"
    assert result["job"]["company"] == ""
    assert result["missing_fields"] == ["company", "location"]
    assert result["job"]["description"] == "Research proteins."


def test_malformed_jsonld_and_javascript_shell_produce_reviewable_draft(tmp_path):
    result = prepare_job_url(URL, output_dir=tmp_path, fetch_fn=lambda url: ('<script type="application/ld+json">{broken}</script><div id="root"></div>', url))
    assert not result["complete"]
    assert set(result["missing_fields"]) == {"company", "title", "location", "description"}
    assert result["yaml_path"].is_file()
    assert yaml.safe_load(result["yaml_path"].read_text(encoding="utf-8"))["description"] == ""
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["model_used"] is False
    assert audit["needs_review"] is True
    assert audit["sources"][0]["sha256"]


def test_http_failure_keeps_url_and_explains_manual_fallback(tmp_path):
    def blocked(url):
        raise FetchError("Website returned HTTP 403. Paste vacancy text into the draft.")
    result = prepare_job_url(URL, output_dir=tmp_path, fetch_fn=blocked)
    assert not result["complete"]
    assert result["job"]["source_url"] == URL
    assert "403" in result["warnings"][0]


def test_ashby_uses_exact_job_feed_without_inventing_company(tmp_path):
    url = "https://jobs.ashbyhq.com/example-bio/id-2"
    calls = []
    def fetch(requested):
        calls.append(requested)
        if requested == url:
            return "<div id='root'></div>", requested
        return json.dumps({"jobs": [
            {"title": "Unrelated Job", "jobUrl": "https://jobs.ashbyhq.com/example-bio/id-1"},
            {"title": "Founders Associate", "jobUrl": url, "location": "Lausanne", "descriptionPlain": "Own operations.", "workplaceType": "Hybrid", "applyUrl": url + "/application"},
        ]}), requested
    result = prepare_job_url(url, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert len(calls) == 2
    assert result["job"]["title"] == "Founders Associate"
    assert result["job"]["work_mode"] == "hybrid"
    assert result["missing_fields"] == ["company"]
    assert result["extraction_method"] == "ashby-public-api"
    assert list(tmp_path.iterdir()) == []
    complete = prepare_job_url(url, output_dir=tmp_path, dry_run=True, overrides={"company": "Example Bio"}, fetch_fn=fetch)
    assert complete["complete"]


def test_greenhouse_feed_uses_returned_company_and_decodes_html(tmp_path):
    url = "https://job-boards.greenhouse.io/example/jobs/123"
    def fetch(requested):
        if requested == url:
            return "<html></html>", requested
        return json.dumps({"id": 123, "title": "Scientist", "company_name": "Example Bio", "location": {"name": "Basel"}, "content": "&lt;p&gt;Design experiments.&lt;/p&gt;"}), requested
    result = prepare_job_url(url, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert result["complete"]
    assert result["job"]["company"] == "Example Bio"
    assert result["job"]["description"] == "Design experiments."


def test_lever_eu_feed_includes_bullets_and_no_invented_company(tmp_path):
    url = "https://jobs.eu.lever.co/example/abc-123"
    def fetch(requested):
        if requested == url:
            return "<html><title>Scientist @ Example Bio</title><h1>Scientist</h1></html>", requested
        assert requested == "https://api.eu.lever.co/v0/postings/example/abc-123"
        return json.dumps({"id": "abc-123", "text": "Scientist", "categories": {"location": "Basel"}, "descriptionPlain": "Research proteins.", "lists": [{"text": "Requirements", "content": "<li>Scientific experience.</li>"}]}), requested
    result = prepare_job_url(url, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert result["complete"]
    assert "Scientific experience." in result["job"]["description"]


def test_existing_draft_is_not_overwritten_and_default_names_are_unique(tmp_path):
    target = tmp_path / "job.yaml"
    target.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        prepare_job_url(URL, output_dir=tmp_path, output_path=target, fetch_fn=lambda url: pytest.fail("Should fail before fetching"))
    assert target.read_text(encoding="utf-8") == "keep me"
    first = prepare_job_url(URL, output_dir=tmp_path, fetch_fn=lambda url: (page(job_posting()), url))
    second = prepare_job_url(URL, output_dir=tmp_path, fetch_fn=lambda url: (page(job_posting()), url))
    assert first["yaml_path"] != second["yaml_path"]


@pytest.mark.parametrize("url", [
    "file:///c:/secret", "http://localhost/job", "http://127.0.0.1/job", "http://10.0.0.1/job",
    "http://169.254.169.254/latest", "http://[::1]/job", "https://user:password@example.com/job",
    "https://example.com:8080/job", "https://example.internal/job", "https://example.com/\nsecret",
])
def test_unsafe_urls_rejected_before_fetch(tmp_path, url):
    with pytest.raises(ValueError):
        prepare_job_url(url, output_dir=tmp_path, fetch_fn=lambda value: pytest.fail("Unsafe URL must not be fetched"))
    assert list(tmp_path.iterdir()) == []


def test_private_dns_and_mixed_public_private_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.1", 443)),
    ])
    with pytest.raises(ValueError, match="private"):
        validate_public_url(URL, resolve=True)


def test_callback_unsafe_final_url_is_not_parsed(tmp_path):
    result = prepare_job_url(URL, output_dir=tmp_path, dry_run=True, fetch_fn=lambda url: (page(job_posting()), "http://127.0.0.1/private"))
    assert not result["complete"]
    assert result["job"]["company"] == ""
    assert "private" in result["warnings"][0]


def test_invalid_untrusted_apply_url_is_omitted(tmp_path):
    url = "https://jobs.ashbyhq.com/example/id-2"
    def fetch(requested):
        if requested == url:
            return "", requested
        return json.dumps({"jobs": [{"jobUrl": url, "title": "Scientist", "location": "Basel", "descriptionPlain": "Research.", "applyUrl": "file:///secret"}]}), requested
    result = prepare_job_url(url, output_dir=tmp_path, dry_run=True, fetch_fn=fetch)
    assert "apply_url" not in result["job"]
    assert any("invalid application URL" in warning for warning in result["warnings"])


def test_hidden_and_script_content_is_never_copied():
    assert html_to_text('<p>Real description</p><div hidden>Secret text</div><script>execute()</script>') == "Real description"


def test_success_status_browser_challenge_is_not_a_job():
    result = parse_job_page('<title>Just a moment...</title><h1>Verify you are human</h1><main>Complete the challenge.</main>', URL)
    assert result["extraction_method"] == "blocked-page"
    assert not result["complete"]
    assert result["job"]["description"] == ""


def test_multiple_unstructured_headings_are_not_combined_into_a_title():
    result = parse_job_page('<h1>Open vacancies</h1><h1>Scientist</h1><main>Browse all jobs.</main>', URL)
    assert result["job"]["title"] == ""


def test_fetch_revalidates_redirect_before_opening_private_address(monkeypatch):
    opened = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    class Response:
        status = 302
        def getheader(self, name, default=None):
            return "http://127.0.0.1/secret" if name == "Location" else default
    class Connection:
        def __init__(self, host, port, address, timeout):
            opened.append((host, address))
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(url_import, "_PinnedHTTPSConnection", Connection)
    with pytest.raises(ValueError, match="private"):
        url_import.fetch_public_page(URL)
    assert opened == [("careers.example.com", "93.184.216.34")]


def test_fetch_rejects_oversized_page_without_reading_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    class Response:
        status = 200
        def getheader(self, name, default=None):
            return {"Content-Type": "text/html", "Content-Length": str(url_import.MAX_BYTES + 1)}.get(name, default)
        def read1(self, *args):
            pytest.fail("An oversized body should not be downloaded")
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(url_import, "_PinnedHTTPSConnection", Connection)
    with pytest.raises(FetchError, match="2 MB"):
        url_import.fetch_public_page(URL)


def test_microdata_meta_fields_supply_literal_employer_and_nested_location():
    html = '''<div itemscope itemtype="http://schema.org/JobPosting">
      <span itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">
        <span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
          <meta itemprop="streetAddress" content="Zurich (Opfikon), CH">
        </span>
      </span>
      <meta itemprop="hiringOrganization" content="Example Consulting">
      <meta itemprop="datePosted" content="2026-09-19">
      <h1 itemprop="title">Strategy and Transformation Consultant</h1>
      <section itemprop="description"><p>Deliver business strategy and AI projects.</p></section>
    </div>'''
    result = parse_job_page(html, URL)
    assert result["complete"]
    assert result["extraction_method"] == "microdata"
    assert result["job"]["company"] == "Example Consulting"
    assert result["job"]["location"] == "Zurich (Opfikon), CH"
    assert result["job"]["posted_date"] == "2026-09-19"
    assert result["job"]["description"] == "Deliver business strategy and AI projects."


def test_redirect_to_careers_listing_does_not_make_listing_title_a_job(tmp_path):
    final = "https://careers.example.com/careers"
    html = "<title>Career Search | Example</title><h1>Career Search</h1><main>Browse current opportunities.</main>"
    result = prepare_job_url(URL, output_dir=tmp_path, dry_run=True, fetch_fn=lambda _: (html, final))
    assert not result["complete"]
    assert result["extraction_method"] == "listing-page"
    assert result["job"]["title"] == ""
    assert result["job"]["source_url"] == URL
    assert result["final_url"] == final
    assert "may have closed" in result["warnings"][0]


def test_multiple_microdata_jobs_are_not_combined_into_one_complete_vacancy():
    item = '''<article itemscope itemtype="https://schema.org/JobPosting">
      <meta itemprop="hiringOrganization" content="Example Bio">
      <meta itemprop="jobLocation" content="Lausanne">
      <h1 itemprop="title">Scientist</h1>
      <div itemprop="description">Study proteins.</div>
    </article>'''
    result = parse_job_page(item + item.replace("Scientist", "Engineer"), URL)
    assert not result["complete"]
    assert result["extraction_method"] == "ambiguous"
    assert result["job"]["title"] == ""
