import json

import pytest

from job_radar import discovery, freshness, ranking
from job_radar.db import JobDatabase
from test_discovery import ASHBY, GREENHOUSE, preset
from test_gui import app


NOW = freshness.instant("2026-11-21T12:00:00Z")


@pytest.mark.parametrize(("published", "expected"), [
    ("2026-11-19T13:00:00+01:00", "Fresh"), ("2026-11-19T11:59:59Z", "Older"),
    ("2026-11-19", "Older"), ("2026-11-22", "Date unknown"), ("invalid", "Date unknown"),
    (None, "Date unknown"), ("2026-11-20T12:00:00", "Date unknown"),
])
def test_elapsed_hours(published, expected):
    assert freshness.category({"published_at": published}, 48, NOW) == expected


@pytest.mark.parametrize("title", ["Internship in research", "Lab Intern", "Traineeship",
                                   "Graduate Programme", "Spontaneous application", "Open application"])
def test_programs_exempt_even_without_publication(title):
    assert freshness.category({"title": title}, 48, NOW) == "Programs"
    assert freshness.category({"title": title, "posted_date": "2020-01-01"}, 48, NOW) == "Programs"


def test_program_type_and_false_positives():
    assert freshness.category({"employment_type": "Intern"}, 48, NOW) == "Programs"
    assert freshness.category({"title": "Internal Auditor", "description": "Mentor interns"}, 48, NOW) == "Date unknown"


def test_window_preferences_preserve_existing_terms(tmp_path):
    ranking.save_terms(tmp_path, "biology")
    path = tmp_path / "data/discovery_preferences.json"
    path.write_text(json.dumps({"version": 1, "terms": "biology"}))
    assert ranking.load_hours(tmp_path) == 48
    ranking.save_hours(tmp_path, "72")
    with pytest.raises(ValueError):
        ranking.save_hours(tmp_path, "0")
    assert ranking.load_terms(tmp_path) == "biology"
    ranking.save_terms(tmp_path, "research")
    assert ranking.load_hours(tmp_path) == 72


def test_edits_cannot_turn_unknown_age_into_recent_publication(tmp_path, monkeypatch):
    preset(tmp_path, "greenhouse")
    payload = {"jobs": [GREENHOUSE]}
    monkeypatch.setattr(discovery, "_fetch", lambda _: payload)
    monkeypatch.setattr(discovery, "utc_now", lambda: "2026-11-01T10:00:00Z")
    original = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert original["published_at"] is None
    monkeypatch.setattr(discovery, "utc_now", lambda: NOW.isoformat())
    payload["jobs"] = [{**GREENHOUSE, "first_published": "2026-11-21T09:00:00Z"}]
    changed = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert freshness.category(changed, 48, NOW) == "Date unknown"
    assert changed["first_seen_at"] == original["first_seen_at"]
    # An earlier, verifiable date can still fill the gap.
    payload["jobs"] = [{**GREENHOUSE, "first_published": "2026-10-31"}]
    dated = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert dated["published_at"] == "2026-10-31"
    assert freshness.category(dated, 48, NOW) == "Older"


def test_refresh_return_and_import_preserve_original_dates(tmp_path, monkeypatch):
    preset(tmp_path, "greenhouse")
    payload = {"jobs": [{**GREENHOUSE, "first_published": "2025-01-01T09:00:00Z", "internal_job_id": 42}]}
    monkeypatch.setattr(discovery, "_fetch", lambda _: payload)
    monkeypatch.setattr(discovery, "utc_now", lambda: "2025-02-01T12:00:00+00:00")
    first = discovery.refresh_presets(tmp_path)["candidates"][0]
    payload["jobs"] = []
    assert not discovery.refresh_presets(tmp_path)["candidates"]
    monkeypatch.setattr(discovery, "utc_now", lambda: "2025-03-05T12:00:00+00:00")
    payload["jobs"] = [{**GREENHOUSE, "id": 456, "absolute_url": GREENHOUSE["absolute_url"] + "456",
                        "internal_job_id": 42, "first_published": "2025-03-05T11:00:00Z"}]
    repost = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert first["id"] != repost["id"]
    assert repost["published_at"] == first["published_at"]
    assert repost["first_seen_at"] == first["first_seen_at"]
    assert repost["last_verified_at"] == "2025-03-05T12:00:00+00:00"
    db = JobDatabase(tmp_path / "tracker.sqlite3")
    job_id, _ = discovery.import_candidate(db, repost)
    saved = db.get_job(job_id)
    assert saved["posted_date"] == "2025-01-01"
    for key in ("first_seen_at", "last_verified_at"):
        assert saved[key] == repost[key]


def test_legacy_cache_and_possible_repost(tmp_path, monkeypatch):
    board = preset(tmp_path)
    old = discovery._candidate(board, ASHBY, "2026-10-01T09:00:00Z")
    old.pop("published_at")
    path = tmp_path / "data/discovery_cache.json"
    path.write_text(json.dumps({"version": 1, "boards": {board["id"]: {"candidates": [old]}}}))
    stamp = "2026-11-21T12:00:00+00:00"
    payload = {"jobs": [ASHBY]}
    monkeypatch.setattr(discovery, "utc_now", lambda: stamp)
    monkeypatch.setattr(discovery, "_fetch", lambda _: payload)
    row = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert row["first_seen_at"].startswith("2026-10-01")
    assert row["published_at"].startswith("2026-09-30T09:")
    payload["jobs"] = [{**ASHBY, "publishedAt": stamp}]
    assert freshness.category(discovery.refresh_presets(tmp_path)["candidates"][0], 48, NOW) == "Older"
    payload["jobs"] = [{**ASHBY, "jobUrl": ASHBY["jobUrl"] + "-again", "publishedAt": stamp}]
    repost = discovery.refresh_presets(tmp_path)["candidates"][0]
    assert repost["possible_repost"]
    assert freshness.category(repost, 48, NOW) == "Date unknown"


def test_visible_feed_filters_and_age_preference(app):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    app.candidates = [dict(id=title, title=title, company="Example", location="Basel",
                           posted_date=(now - timedelta(hours=age)).isoformat() if age else None)
                      for title, age in (("Analyst", 2), ("Lab intern", 100), ("Editor", None), ("Scientist", 60))]
    for view, expected in [("All ages", ("Analyst", "Lab intern", "Editor", "Scientist")),
                           ("Fresh", ("Analyst", "Lab intern")), ("Programs", ("Lab intern",)),
                           ("Date unknown", ("Editor",)), ("Older", ("Scientist",))]:
        app.age_filter.set(view)
        app._fill_candidates()
        assert app.candidate_tree.get_children() == expected
    app.age_entry.set("72")
    app.apply_age_window()
    app.age_filter.set("Fresh")
    app._fill_candidates()
    assert app.candidate_tree.get_children() == ("Analyst", "Scientist", "Lab intern")
