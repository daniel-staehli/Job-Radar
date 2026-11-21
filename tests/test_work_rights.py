from datetime import date
import json

import pytest
import yaml

from job_radar import work_rights as rights, job_match
from test_gui import app
from test_revisions import profile as fictional_cv


def entry(status, *, expires="", permit="", visa=""):
    return dict(status=status, expires=expires, permit=permit, visa=visa)


def check(location, citizens=(), *, records=None, text="", preference=None, **fields):
    p = rights.empty()
    p.update(citizenships=list(citizens), rights=records or {}, preference=preference or p["preference"])
    return rights.assess(dict(location=location, description=text, **fields), p, date(2026, 11, 21))


@pytest.mark.parametrize("location,citizens,status", [
    ("Basel, Switzerland", ["CH"], rights.AUTHORIZED), ("Zürich", ["FR"], rights.AUTHORIZED),
    ("Italy", ["CH"], rights.AUTHORIZED), ("Norway", ["IS"], rights.AUTHORIZED),
    ("Liechtenstein", ["FR"], rights.VERIFY), ("Liechtenstein", ["LI"], rights.AUTHORIZED),
    ("London, UK", ["IE"], rights.AUTHORIZED), ("Ireland", ["GB"], rights.AUTHORIZED),
    ("London, UK", ["DE"], rights.VERIFY), ("Remote (US)", ["US"], rights.AUTHORIZED),
    ("Singapore", ["SG"], rights.AUTHORIZED), ("Doha", ["QA"], rights.AUTHORIZED),
    ("Australia", ["NZ"], rights.VERIFY), ("New Zealand", ["AU"], rights.VERIFY),
    ("Dover, DE", ["DE"], rights.VERIFY), ("Remote", ["CH"], rights.VERIFY),
    ("US or Singapore", ["US"], rights.VERIFY), ("US, Canada", ["US"], rights.VERIFY),
])
def test_country_routes(location, citizens, status):
    assert check(location, citizens).status == status


def test_document_type_alone_cannot_establish_rights():
    for status in rights.STATUSES[:3]:
        result = check("Singapore", records={"SG": entry(status, visa="Example pass")})
        assert (result.status == rights.AUTHORIZED) == (status == rights.STATUSES[1])
    assert check("Singapore", records={"SG": entry(rights.STATUSES[1], expires="2026-11-20")}).status == rights.VERIFY
    assert check("Singapore", records={"SG": entry(rights.STATUSES[1], expires="2026-11-21")}).status == rights.AUTHORIZED
    assert check("Singapore", records={"CH": entry(rights.STATUSES[1])}).status == rights.VERIFY


@pytest.mark.parametrize("text", [
    "", "Visa sponsorship is available.", "Will you require sponsorship?",
    "No sponsorship available for internships only.", "No sponsorship available. Exceptions are possible.",
    "No sponsorship available. We can sponsor some candidates.", "No sponsorship available. We can help with visas.",
    "No sponsorship available. Must hold a specific work permit.",
])
def test_preserve_ambiguous_sponsorship(text):
    assert check("United States", records={"US": entry(rights.STATUSES[3])}, text=text).status == rights.VERIFY


def test_exclusion_requires_both_clear_statements_and_one_current_location():
    text = "Research role. We do not provide visa sponsorship for this role."
    rows = {"GB": entry(rights.STATUSES[3])}
    assessed = check("London, UK", records=rows, text=text)
    assert assessed.status == rights.EXCLUDED and "We do not provide" in assessed.details()
    assert check("London, UK", text=text, preference=rights.PREFERENCES[2]).status == rights.VERIFY
    assert check("London, UK", records=rows, text=text, stale=True).status == rights.VERIFY
    assert check("London, UK", records=rows, text=text, secondary_locations=["Paris"]).status == rights.VERIFY
    assert check("London, UK", records=rows, text=text, evidence=["Other published locations: Paris"]).status == rights.VERIFY
    assert check("US", ["US"], text="An active security clearance is required.").status == rights.VERIFY
    assert check("CH", ["CH"], text="You must have the right to work in the UK.").status == rights.VERIFY
    assert check("UK", ["GB"], records=rows, text=text).status == rights.VERIFY


@pytest.mark.parametrize("sentence", ["Kein Visa-Sponsoring möglich.", "Aucun parrainage de visa disponible.",
                                      "We are unable to sponsor visas for this role."])
def test_clear_sponsorship_restrictions(sentence):
    report = check("Switzerland", records={"CH": entry(rights.STATUSES[3])}, text=sentence)
    assert report.status == rights.EXCLUDED and sentence in report.evidence


def test_separate_storage_and_stale_save(tmp_path):
    p, fingerprint = rights.load(tmp_path)
    assert not (tmp_path / "data").exists()
    p["rights"]["US"] = entry(rights.STATUSES[2], visa="Fictional test document")
    rights.save(tmp_path, p, fingerprint)
    assert rights.load(tmp_path)[0] == p
    assert [f.name for f in (tmp_path / "data").iterdir()] == [rights.FILE]
    with pytest.raises(ValueError, match="another window"):
        rights.save(tmp_path, rights.empty(), fingerprint)
    (tmp_path / "data/master_profile.yaml").write_text(yaml.safe_dump(fictional_cv()), encoding="utf-8")
    assert "Fictional test document" not in json.dumps(job_match._profile_snapshot(tmp_path))
    p["rights"]["US"]["expires"] = "wrong"
    with pytest.raises(ValueError):
        rights.save(tmp_path, p, rights.load(tmp_path)[1])
    assert rights.load(tmp_path)[0]["rights"]["US"]["expires"] == ""


def test_gui_profile_and_shortlist(app):
    app.candidates = [dict(id=str(i), title="Scientist", company="Example", location=place,
                           description="No visa sponsorship available.", provider="ashby", source_url="example")
                      for i, place in enumerate(("USA", "Switzerland", "Remote"))]
    dialog = app.open_work_rights()
    dialog.citizens.set("Switzerland")
    dialog.destination.set("United States")
    dialog.fields["status"].set(rights.STATUSES[3])
    dialog.destination.set("Singapore")
    dialog.fields["permit"].set("Example")
    dialog.save()
    saved, _ = rights.load(app.service.root)
    assert saved["rights"]["SG"]["permit"] == "Example"
    assert set(app.candidate_tree.get_children()) == {"1", "2"}
    assert app.candidate_tree.set("1", "rights") == rights.AUTHORIZED
    app.show_ineligible.set(True)
    app._fill_candidates()
    assert app.candidate_tree.set("0", "rights") == rights.EXCLUDED
    app.candidate_tree.selection_set("0")
    app._show_candidate()
    assert "No visa sponsorship available." in app.candidate_viewer.get("1.0", "end")
    dialog = app.open_work_rights()
    dialog.destination.set("United States")
    dialog.clear_country()
    dialog.save()
    assert app.candidate_tree.set("0", "rights") == rights.VERIFY


def test_saved_job_remains_in_tracker(app):
    job_id, _ = app.service.save_job({"company": "Example", "title": "Researcher", "location": "Singapore",
                                     "description": "No visa sponsorship available."})
    app.refresh()
    app.job_tree.selection_set(job_id)
    app._job_selected()
    dialog = app.open_work_rights()
    dialog.destination.set("Singapore")
    dialog.fields["status"].set(rights.STATUSES[3])
    dialog.save()
    assert app.job_tree.exists(job_id)
    assert app.job_rights.get().endswith(rights.EXCLUDED)
    assert app.service.jobs()[0]["status"] == "interested"
