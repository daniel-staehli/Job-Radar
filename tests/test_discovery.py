import json

import httpx
import pytest

from job_radar import discovery
from job_radar.db import JobDatabase
from job_radar.manual_import import import_manual_data
from job_radar.tracking import get_application, list_events, update_application


ASHBY = {
    "title": "Research Coordinator", "location": "Lausanne, Switzerland",
    "jobUrl": "https://jobs.ashbyhq.com/fictional/11111111-2222-3333-4444-555555555555",
    "applyUrl": "https://jobs.ashbyhq.com/fictional/11111111-2222-3333-4444-555555555555/application",
    "descriptionPlain": "Coordinate scientific studies and laboratory deliveries.",
    "isListed": True, "workplaceType": "Hybrid", "employmentType": "FullTime",
    "publishedAt": "2026-09-30T09:00:00Z",
    "address": {"postalAddress": {"addressCountry": "Switzerland"}},
    "secondaryLocations": [{"location": "Zurich, Switzerland"}],
}
GREENHOUSE = {
    "id": 123, "title": "Research Coordinator", "location": {"name": "Lausanne, Switzerland"},
    "absolute_url": "https://job-boards.greenhouse.io/fictional/jobs/123",
    "content": "&lt;p&gt;Plan studies.&lt;/p&gt;&lt;p&gt;Help researchers.&lt;/p&gt;",
    "updated_at": "2026-09-30T09:00:00Z",
}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Discovery tests must not contact live employer APIs")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def preset(root, provider="ashby"):
    return discovery.save_preset(root, {"company": "Fictional Lab", "provider": provider, "board": "fictional"})


def feed(monkeypatch, payload, *, status=200):
    requests = []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 10
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(discovery.httpx, "Client", client)
    return requests


def test_presets_validate_normalize_and_do_not_fetch(tmp_path):
    assert discovery.list_presets(tmp_path) == []
    assert discovery.list_candidates(tmp_path) == []
    assert not list(tmp_path.iterdir())
    saved = discovery.save_preset(tmp_path, {"company": "  Fictional Lab  ", "provider": "Ashby",
                                            "board": "https://jobs.ashbyhq.com/fictional?utm_source=example"})
    assert saved["board"] == "fictional"
    assert saved["provider"] == "ashby"
    assert saved["company"] == "Fictional Lab"
    assert discovery.save_preset(tmp_path, {**saved, "company": "New label"})["id"] == saved["id"]
    assert len(discovery.list_presets(tmp_path)) == 1
    discovery.remove_preset(tmp_path, saved["id"])
    assert discovery.list_presets(tmp_path) == []


@pytest.mark.parametrize("board", ["https://evil.invalid/fictional", "https://jobs.ashbyhq.com.evil.invalid/a",
                                   "https://user:password@jobs.ashbyhq.com/fictional",
                                   "https://jobs.ashbyhq.com:9999/fictional", "../fictional", "", "foo?x=bar"])
def test_rejects_arbitrary_urls_and_board_injection(tmp_path, board):
    with pytest.raises(ValueError):
        discovery.save_preset(tmp_path, {"company": "Example", "provider": "ashby", "board": board})
    assert not list(tmp_path.iterdir())


def test_refresh_ashby_is_explicit_and_only_caches_listed_jobs(tmp_path, monkeypatch):
    saved = preset(tmp_path)
    calls = feed(monkeypatch, {"jobs": [ASHBY, {**ASHBY, "isListed": False}, {**ASHBY, "isListed": None}]})
    assert discovery.list_candidates(tmp_path) == []
    assert not calls
    result = discovery.refresh_presets(tmp_path)
    assert not result["errors"]
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.ashbyhq.com/posting-api/job-board/fictional"
    assert "authorization" not in calls[0].headers
    candidates = result["candidates"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["preset_id"] == saved["id"]
    assert candidate["posted_date"] == "2026-09-30"
    assert candidate["work_mode"] == "hybrid"
    assert candidate["secondary_locations"] == ["Zurich, Switzerland"]
    assert candidate["country"] == "Switzerland"
    assert candidate["importable"] and not candidate["stale"]
    assert not list(tmp_path.rglob("*.sqlite3"))
    assert discovery.list_candidates(tmp_path) == candidates
    assert len(calls) == 1
    discovery.remove_preset(tmp_path, saved["id"])
    assert discovery.list_candidates(tmp_path) == []
    assert not json.loads((tmp_path / "data/discovery_cache.json").read_text())["boards"]


def test_greenhouse_identity_and_missing_dates(tmp_path, monkeypatch):
    preset(tmp_path, "greenhouse")
    calls = feed(monkeypatch, {"jobs": [GREENHOUSE]})
    result = discovery.refresh_presets(tmp_path)
    row = result["candidates"][0]
    assert str(calls[0].url) == "https://boards-api.greenhouse.io/v1/boards/fictional/jobs?content=true"
    assert row["external_id"] == "123"
    assert row["posted_date"] is None
    assert row["work_mode"] == "unknown"
    assert row["description"] == "Plan studies.\nHelp researchers."


@pytest.mark.parametrize("failure", [429, 503, 302, {"unexpected": []}, {"jobs": [None]}])
def test_failed_refresh_keeps_stale_cached_results(tmp_path, monkeypatch, failure):
    preset(tmp_path)
    monkeypatch.setattr(discovery, "_fetch", lambda item: {"jobs": [ASHBY]})
    before = discovery.refresh_presets(tmp_path)["candidates"][0]

    def failed(item):
        if isinstance(failure, dict):
            # Exercise production format validation for unsupported responses.
            if "jobs" not in failure:
                raise ValueError("Unsupported feed format")
            return failure
        response = httpx.Response(failure, request=httpx.Request("GET", "https://api.ashbyhq.com/example"))
        response.raise_for_status()
    monkeypatch.setattr(discovery, "_fetch", failed)
    result = discovery.refresh_presets(tmp_path)
    assert len(result["errors"]) == 1
    assert result["candidates"][0]["id"] == before["id"]
    assert result["candidates"][0]["fetched_at"] == before["fetched_at"]
    assert result["candidates"][0]["stale"]
    assert result["candidates"][0]["refresh_error"]


@pytest.mark.parametrize("status", [302, 429, 404])
def test_http_failure_does_not_follow_redirect_or_retry(tmp_path, monkeypatch, status):
    preset(tmp_path)
    calls = feed(monkeypatch, {"jobs": []}, status=status)
    result = discovery.refresh_presets(tmp_path)
    assert len(calls) == 1
    assert len(result["errors"]) == 1
    assert result["candidates"] == []


def test_http_download_and_job_count_are_bounded(tmp_path, monkeypatch):
    preset(tmp_path)
    feed(monkeypatch, {"jobs": [ASHBY]})
    monkeypatch.setattr(discovery, "MAX_FEED_BYTES", 10)
    assert "size" in discovery.refresh_presets(tmp_path)["errors"][0]["message"]
    monkeypatch.setattr(discovery, "MAX_FEED_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(discovery, "MAX_FEED_JOBS", 0)
    assert "too many" in discovery.refresh_presets(tmp_path)["errors"][0]["message"]


def test_timeout_and_non_json_are_reviewable(tmp_path, monkeypatch):
    preset(tmp_path)
    original = httpx.Client
    def timeout(request):
        raise httpx.ReadTimeout("synthetic", request=request)
    monkeypatch.setattr(discovery.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(timeout), **kw))
    assert "connect" in discovery.refresh_presets(tmp_path)["errors"][0]["message"]
    monkeypatch.setattr(discovery.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>not a feed</html>")), **kw))
    assert "valid JSON" in discovery.refresh_presets(tmp_path)["errors"][0]["message"]


def test_import_external_id_preserves_history_and_distinguishes_requisitions(tmp_path):
    saved = preset(tmp_path, "greenhouse")
    candidate = discovery._candidate(saved, GREENHOUSE, "2026-09-30T12:00:00Z")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    first_id, action = discovery.import_candidate(db, candidate)
    assert action == "new"
    assert discovery.import_candidate(db, candidate) == (first_id, "unchanged")
    update_application(db, first_id, {"status": "applied", "cv_version": "test-v1.pdf"})
    events = list_events(db)
    changed = discovery._candidate(saved, {**GREENHOUSE, "title": "New role name",
                                          "location": {"name": "Zurich"}, "content": "New content"}, "later")
    assert discovery.import_candidate(db, changed) == (first_id, "changed")
    assert db.get_job(first_id)["title"] == "New role name"
    assert get_application(db, first_id)["status"] == "applied"
    assert get_application(db, first_id)["cv_version"] == "test-v1.pdf"
    assert list_events(db) == events
    second = discovery._candidate(saved, {**GREENHOUSE, "id": 124,
                                         "absolute_url": "https://job-boards.greenhouse.io/fictional/jobs/124"}, "later")
    # Restore the original title/location for a real identical-title case.
    discovery.import_candidate(db, candidate)
    second_id, action = discovery.import_candidate(db, second)
    assert action == "new" and second_id != first_id
    assert len(db.list_jobs()) == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id=?", (first_id,)).fetchone()[0] == 3


def test_manual_url_collision_does_not_rebind_or_modify_history(tmp_path):
    saved = preset(tmp_path)
    candidate = discovery._candidate(saved, ASHBY, "now")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    manual = {key: candidate[key] for key in ("company", "title", "location", "source_url", "description")}
    existing_id, _ = import_manual_data(db, manual)
    update_application(db, existing_id, {"status": "applied"})
    before = db.path.read_bytes()
    with pytest.raises(ValueError, match="No records were merged or changed"):
        discovery.import_candidate(db, candidate)
    assert db.path.read_bytes() == before
    assert len(db.list_jobs()) == 1


def test_same_title_manual_record_is_not_silently_rebound(tmp_path):
    saved = preset(tmp_path)
    candidate = discovery._candidate(saved, ASHBY, "now")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    manual = {key: candidate[key] for key in ("company", "title", "location", "description")}
    original, _ = import_manual_data(db, {**manual, "source_url": "https://example.invalid/another-requisition"})
    imported, _ = discovery.import_candidate(db, candidate)
    assert imported != original
    assert len(db.list_jobs()) == 2


def test_empty_successful_feed_does_not_close_tracked_jobs(tmp_path, monkeypatch):
    saved = preset(tmp_path)
    candidate = discovery._candidate(saved, ASHBY, "now")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    job_id, _ = discovery.import_candidate(db, candidate)
    before = db.path.read_bytes()
    monkeypatch.setattr(discovery, "_fetch", lambda item: {"jobs": []})
    assert discovery.refresh_presets(tmp_path)["candidates"] == []
    assert db.path.read_bytes() == before
    assert db.get_job(job_id)["verification_status"] != "closed"


def test_missing_fields_stay_unknown_and_candidate_requires_manual_completion(tmp_path):
    saved = preset(tmp_path, "greenhouse")
    candidate = discovery._candidate(saved, {**GREENHOUSE, "location": None, "content": None}, "now")
    assert not candidate["importable"]
    assert candidate["missing_fields"] == ["location", "description"]
    assert candidate["location"] == "" and candidate["description"] == ""
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="Review in manual form"):
        discovery.import_candidate(db, candidate)
    assert not db.path.exists()


def test_manual_review_corrections_preserve_external_identity(tmp_path):
    saved = preset(tmp_path, "greenhouse")
    candidate = discovery._candidate(saved, {**GREENHOUSE, "location": None, "content": None}, "now")
    reviewed = {**candidate, "company": "Corrected employer", "title": "Reviewed title",
                "location": "Lausanne", "description": "User supplied missing description",
                "role_family": "Research operations"}
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    job_id, action = discovery.import_candidate(db, reviewed)
    assert action == "new"
    assert job_id.endswith(candidate["id"].removeprefix("candidate_"))
    assert db.get_job(job_id)["company"] == "Corrected employer"
    assert db.get_job(job_id)["role_family"] == "Research operations"


def test_candidate_identity_tampering_is_rejected(tmp_path):
    saved = preset(tmp_path)
    candidate = discovery._candidate(saved, ASHBY, "now")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    for change in ({"external_id": "wrong"}, {"id": "candidate_wrong"}, {"board": "different"},
                   {"source_url": "https://example.invalid/other"}):
        with pytest.raises(ValueError):
            discovery.import_candidate(db, {**candidate, **change})
    assert not db.path.exists()
    with pytest.raises(ValueError, match="connector identity"):
        import_manual_data(db, {}, identity_key="job_anything")


def test_corrupted_saved_file_is_not_overwritten(tmp_path):
    path = tmp_path / "data/employer_presets.json"
    path.parent.mkdir()
    path.write_text("broken")
    with pytest.raises(ValueError, match="not overwritten"):
        preset(tmp_path)
    assert path.read_text() == "broken"


def test_refresh_only_selected_presets_and_unknown_selection(tmp_path, monkeypatch):
    first = preset(tmp_path)
    discovery.save_preset(tmp_path, {"company": "Second", "provider": "greenhouse", "board": "second"})
    seen = []
    def fetch(item):
        seen.append(item["id"])
        return {"jobs": []}
    monkeypatch.setattr(discovery, "_fetch", fetch)
    discovery.refresh_presets(tmp_path, [first["id"]])
    assert seen == [first["id"]]
    with pytest.raises(ValueError, match="no longer exist"):
        discovery.refresh_presets(tmp_path, ["board_missing"])
