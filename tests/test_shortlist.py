from datetime import datetime, timedelta, timezone

import pytest

from job_radar import discovery_match, network, shortlist, work_rights
from test_discovery_match import candidate, setup
from test_gui import app


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def prioritize(job, **options):
    defaults = dict(now=NOW, hours=48, profile=dict(work_rights.empty(), citizenships=["CH"]),
                    groups=[["research"]], network=network.Network())
    return shortlist.evaluate(job, **{**defaults, **options})


def posting(**changes):
    return dict(candidate(), published_at=NOW.isoformat()) | changes


def review(category, **changes):
    return {"assessment": {"category": category}, **changes}


def test_priority_factors_and_tie_breakers(tmp_path):
    network.save_contact(tmp_path, {"name": "Example Contact", "company": "Connected", "strength": "Colleague"})
    contacts = network.Network(tmp_path)
    jobs = [posting(id="older", published_at="2020-01-10"), posting(id="term", description="Research in Python"),
            posting(id="unknown", location="Worldwide"), posting(id="network", company="Connected"),
            posting(id="program", title="Lab intern", published_at="2020-01-10"), posting(id="base")]
    def ordered(items):
        return [j["id"] for j in sorted(items, key=lambda j: prioritize(
            j, network=contacts, groups=[["research"], ["python"]]).sort_key)]
    for j in jobs:
        if j["id"] != "term":
            j["description"] = "Research experience"
    expected = ["term", "network", "base", "unknown", "program", "older"]
    assert ordered(jobs) == ordered(reversed(jobs)) == expected
    assert "strongest: Colleague" in prioritize(jobs[3], network=contacts).explanation()
    assert prioritize(jobs[-2]).label == "Recommended"
    assert prioritize(jobs[0]).label == "Review later"
    assert "exempt" in prioritize(jobs[-2]).explanation()
    assert prioritize(jobs[2]).label == "Verify details"


@pytest.mark.parametrize("saved", [None, review("poor", stale=True), {"error": "Unavailable"},
                                  review("insufficient_evidence")])
def test_missing_or_unusable_reviews_are_neutral(saved):
    assert prioritize(posting(), review=saved).sort_key == prioritize(posting()).sort_key


def test_current_review_effects_and_overrides():
    unrelated = posting(description="Budget planning")
    assert prioritize(unrelated).label == "Review later"
    assert prioritize(unrelated, review=review("strong")).label == "Recommended"
    assert prioritize(unrelated, review=review("strong"), use_review=False).label == "Review later"
    assert prioritize(posting(), review=review("poor")).label == "Review later"
    assert prioritize(posting(stale=True), review=review("strong")).label == "Review later"
    assert prioritize(posting(published_at=None, posted_date=None)).label == "Verify details"


def test_entire_batch_checked_locally_before_ollama(setup):
    root, settings, calls = setup
    profile, fingerprint = work_rights.load(root)
    profile["rights"]["US"] = dict(permit="", visa="", expires="", status="Require employer sponsorship")
    work_rights.save(root, profile, fingerprint)
    excluded = dict(candidate(2), location="United States", description="No visa sponsorship available.")
    with pytest.raises(ValueError, match="confirmed ineligible"):
        discovery_match.review_candidates(root, [candidate(1), excluded], settings)
    assert not calls
    assert not (root / "data/job_assessments").exists()


def test_default_shortlist_and_recovering_deferred_roles(app):
    now = datetime.now(timezone.utc)
    fresh = posting(published_at=now.isoformat())
    old = dict(candidate(2), published_at=(now - timedelta(days=10)).isoformat())
    app.candidates = [old, fresh]
    app._fill_candidates()
    assert app.age_filter.get() == "Recommended"
    assert app.candidate_tree.get_children() == (fresh["id"],)
    assert "1 for later" in app.shortlist_summary.get()
    app.age_filter.set("All ages")
    app._fill_candidates()
    assert app.candidate_tree.get_children() == (fresh["id"], old["id"])
    app.candidate_tree.selection_set(old["id"])
    app._show_candidate()
    details = app.candidate_viewer.get("1.0", "end")
    assert "Why this priority" in details and "48-hour window" in details
    assert "AI match: Not reviewed" in details
    assert not app.service.db_path.exists()
