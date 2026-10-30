"""Regression checks for labelled vacancy cards, without network or AI calls."""
import json

import pytest
import yaml

from job_radar.url_import import parse_job_page, prepare_job_url
from test_url_import import job_posting, page as structured_page

URL = "https://careers.example.com/jobs/42-scientific-partnerships-lead"
TITLE = "Scientific Partnerships Lead"
LOCATION = "Porto, Portugal"
EMPLOYER = "Example Research"


def header(*, title=TITLE, location=LOCATION, identifier="EX-42", extra="", heading_tag="h2"):
    return (
        '<div class="page-copy job-details"><div class="small-12 title-card">'
        f'<{heading_tag}>{title}</{heading_tag}>'
        '<p><strong>Updated:</strong> October 21, 2026<br>'
        f'<strong>Location:</strong> <span>{location}</span><br>'
        f'<strong>Job ID:</strong> <span>{identifier}</span>{extra}</p>'
        '</div></div>'
    )


def description(text="Coordinate research programmes and external partnerships."):
    return (
        '<div class="job-details"><div class="job-details__content-description">'
        f'<p>Description</p><p>{text}</p>'
        '<h3>Requirements</h3><ul><li>Research experience.</li><li>Clear communication.</li></ul>'
        '</div><div class="job-details__share">Share this vacancy</div></div>'
    )


def labelled_page(*, card=None, content=None, page_title=None, before="", after=""):
    if card is None:
        card = header()
    if content is None:
        content = description()
    if page_title is None:
        page_title = f"{TITLE} in Porto, Porto - Example\n        Research"
    return (
        f'<html><head><title>{page_title}</title></head><body>'
        '<nav><h2>Search careers</h2>Location: Navigation City</nav>'
        f'{before}{card}{content}{after}'
        '<div><h2>Inside Example Research</h2><p>Company marketing panel.</p></div>'
        '<footer>Location: Footer City</footer></body></html>'
    )


def assert_main_fields(result):
    assert result["job"]["title"] == TITLE
    assert result["job"]["location"] == LOCATION
    assert result["job"]["company"] == EMPLOYER


def test_labelled_job_page_extracts_two_container_layout_and_limits_description():
    result = parse_job_page(labelled_page(), URL)
    assert_main_fields(result)
    assert result["complete"] and result["needs_review"]
    text = result["job"]["description"]
    assert "Coordinate research programmes" in text
    assert "Research experience." in text and "Clear communication." in text
    for excluded in ("Share this vacancy", "Company marketing panel", "Navigation City", "Footer City", "Updated:"):
        assert excluded not in text
    assert "posted_date" not in result["job"], "Updated is not the original posting date"


@pytest.mark.parametrize("heading_tag", ["h1", "h2"])
@pytest.mark.parametrize("title_suffix", [" - Example Research", " in Porto, Porto - Example Research"])
def test_labelled_job_page_accepts_verified_title_conventions(heading_tag, title_suffix):
    result = parse_job_page(labelled_page(card=header(heading_tag=heading_tag), page_title=TITLE+title_suffix), URL)
    assert_main_fields(result)


def test_labelled_job_page_split_tags_and_whitespace_preserve_literal_location():
    card = header(location="&nbsp;<span>Porto,</span>\n\t<span>Portugal</span>&nbsp;")
    result = parse_job_page(labelled_page(card=card), URL)
    assert_main_fields(result)


@pytest.mark.parametrize("wrapper", [
    '<nav>{}</nav>', '<footer>{}</footer>', '<div hidden>{}</div>',
    '<div aria-hidden="true">{}</div>', '<div style="display: none">{}</div>',
    '<div style="visibility: hidden">{}</div>',
])
def test_labelled_job_page_hidden_or_navigation_cards_do_not_compete(wrapper):
    hidden = wrapper.format(header(title="Unrelated position", location="Hidden City", identifier="OTHER-7"))
    result = parse_job_page(labelled_page(before=hidden), URL)
    assert_main_fields(result)
    assert result["complete"]


def test_labelled_job_page_hidden_label_inside_header_does_not_override_workplace():
    extra = '<span hidden><br><strong>Location:</strong> Hidden City</span>'
    result = parse_job_page(labelled_page(card=header(extra=extra)), URL)
    assert_main_fields(result)


def test_labelled_job_page_location_in_description_is_not_header_evidence():
    result = parse_job_page(labelled_page(content=description("Coordinate research. Customer office Location: Paris.")), URL)
    assert result["job"]["location"] == LOCATION
    assert "Paris" in result["job"]["description"]


@pytest.mark.parametrize("extra", [
    '<br><strong>Location:</strong> Lyon, France',
    '<br><strong>Job ID:</strong> OTHER-99',
    '<h2>Unrelated second title</h2>',
])
def test_labelled_job_page_conflicting_header_fields_do_not_choose_first(extra):
    result = parse_job_page(labelled_page(card=header(extra=extra)), URL)
    assert not result["complete"]
    assert result["job"]["title"] == result["job"]["location"] == ""
    assert result["warnings"]


def test_labelled_job_page_competing_visible_cards_are_ambiguous():
    cards = header() + header(title="Another vacancy", location="Lyon, France", identifier="OTHER-7")
    result = parse_job_page(labelled_page(card=cards), URL)
    assert not result["complete"]
    assert result["job"]["title"] == result["job"]["company"] == result["job"]["location"] == ""


def test_labelled_job_page_multiple_description_blocks_are_not_combined():
    result = parse_job_page(labelled_page(content=description()+description("Unrelated other vacancy.")), URL)
    assert not result["complete"]
    assert result["job"]["description"] == ""


@pytest.mark.parametrize("card", [
    f'<div><h2>{TITLE}</h2><p>Location: {LOCATION}<br>Job ID: EX-42</p></div>',
    f'<div class="title-card"><h2>{TITLE}</h2><p>Location: {LOCATION}<br>Job ID: EX-42</p></div>',
    header(identifier=""),
])
def test_labelled_job_page_arbitrary_heading_or_unidentified_card_is_not_a_vacancy(card):
    result = parse_job_page(labelled_page(card=card), URL)
    assert not result["complete"]
    assert result["job"]["title"] == ""
    assert result["job"]["location"] == ""
    assert result["job"]["company"] == ""


def test_labelled_job_page_unmatched_page_title_does_not_supply_employer():
    result = parse_job_page(labelled_page(page_title="Our careers - A Job Board"), URL)
    assert result["job"]["title"] == TITLE
    assert result["job"]["location"] == LOCATION
    assert result["job"]["company"] == ""
    assert "company" in result["missing_fields"]


def test_labelled_job_page_missing_location_does_not_infer_from_page_title():
    result = parse_job_page(labelled_page(card=header(location="")), URL)
    assert result["job"]["title"] == TITLE
    assert result["job"]["location"] == ""
    assert "location" in result["missing_fields"]


def test_labelled_job_page_jsonld_precedence_is_preserved():
    markup = labelled_page(before=structured_page(job_posting()))
    result = parse_job_page(markup, URL)
    assert result["extraction_method"] == "json-ld"
    assert result["job"]["title"] == "Application Scientist"
    assert result["job"]["company"] == "Example Bio"
    assert result["job"]["location"] == "Lausanne, Switzerland"
    assert "Coordinate research" not in result["job"]["description"]


def test_labelled_job_page_microdata_precedence_is_preserved():
    structured = ('<div itemscope itemtype="https://schema.org/JobPosting">'
                  '<span itemprop="title">Verified Scientist</span>'
                  '<span itemprop="hiringOrganization">Verified Employer</span>'
                  '<span itemprop="jobLocation">Oslo, Norway</span>'
                  '<p itemprop="description">Verified role description.</p></div>')
    result = parse_job_page(labelled_page(before=structured), URL)
    assert result["extraction_method"] == "microdata"
    assert result["job"]["title"] == "Verified Scientist"
    assert result["job"]["company"] == "Verified Employer"
    assert result["job"]["location"] == "Oslo, Norway"
    assert result["job"]["description"] == "Verified role description."


@pytest.mark.parametrize("url", [URL, URL+"?tm_job=EX-42&tm_event=view&bid=370"])
def test_labelled_job_page_import_saves_complete_yaml_and_no_ai_audit(tmp_path, url):
    calls = []
    def fetch(requested):
        calls.append(requested)
        return labelled_page(), requested
    result = prepare_job_url(url, output_dir=tmp_path, fetch_fn=fetch)
    assert calls == [url]
    assert result["complete"]
    assert_main_fields(result)
    saved = yaml.safe_load(result["yaml_path"].read_text(encoding="utf-8"))
    assert saved == result["job"]
    assert saved["source_url"] == url
    audit = json.loads(result["audit_path"].read_text(encoding="utf-8"))
    assert audit["model_used"] is False
    assert audit["complete"] is True and audit["missing_fields"] == []
    assert audit["sources"][0]["url"] == url
    assert audit["sources"][0]["sha256"]


@pytest.mark.parametrize("metadata", [
    '<meta property="job:title" content="Another position">',
    '<meta property="job:location" content="Lyon, France">',
])
def test_labelled_job_page_conflicting_explicit_metadata_is_ambiguous(metadata):
    markup = labelled_page().replace('</head>', metadata + '</head>')
    result = parse_job_page(markup, URL)
    assert result["extraction_method"] == "ambiguous"
    assert not result["complete"]
    assert result["job"]["title"] == result["job"]["location"] == ""
    assert any("conflicted" in warning for warning in result["warnings"])


def test_labelled_job_page_scoped_content_overrides_generic_site_heading_and_main():
    markup = labelled_page(
        before='<main><h1>Build your future with us</h1><p>Generic careers marketing.</p>',
        after='<p>More unrelated company details.</p></main>',
    )
    result = parse_job_page(markup, URL)
    assert_main_fields(result)
    assert result["complete"]
    assert "Coordinate research programmes" in result["job"]["description"]
    for unrelated in ("Generic careers marketing", "More unrelated company details", "Build your future"):
        assert unrelated not in result["job"]["description"]


@pytest.mark.parametrize("wrapper", [
    '<div hidden>{}</div>', '<div aria-hidden="true">{}</div>',
    '<div style="display: none">{}</div>', '<div style="visibility: hidden">{}</div>',
])
def test_labelled_job_page_hidden_description_does_not_compete_or_leak(wrapper):
    content = description() + wrapper.format(description("Hidden unrelated vacancy description."))
    result = parse_job_page(labelled_page(content=content), URL)
    assert_main_fields(result)
    assert result["complete"]
    assert "Coordinate research programmes" in result["job"]["description"]
    assert "Hidden unrelated" not in result["job"]["description"]
