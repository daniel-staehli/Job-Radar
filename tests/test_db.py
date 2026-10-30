from job_radar.db import JobDatabase
from job_radar.models import SearchJob


def sample_job(**updates) -> SearchJob:
    data = {
        "company": "Example Bio",
        "title": "Application Scientist",
        "location": "Lausanne, Switzerland",
        "country": "Switzerland",
        "work_mode": "hybrid",
        "employment_type": "Full time",
        "posted_date": "2026-07-28",
        "deadline": None,
        "source_url": "https://example.com/jobs/123?utm_source=test",
        "apply_url": "https://example.com/jobs/123/apply",
        "description_excerpt": "Support imaging customers.",
        "salary_text": None,
        "language_requirements": ["English"],
        "required_years_experience": None,
        "domain": "Scientific imaging",
        "role_family": "Field application scientist",
        "why_fit": ["Microscopy"],
        "hard_gaps": [],
        "functional_fit": 80,
        "hiring_realism": 70,
        "goal_value": 75,
        "evidence": ["PhD accepted"],
    }
    data.update(updates)
    return SearchJob(**data)


def test_upsert_deduplicates_repost_and_versions(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, action = db.upsert_job(sample_job())
    assert action == "new"

    second_id, action = db.upsert_job(
        sample_job(
            source_url="https://example.com/jobs/456?ref=linkedin",
            description_excerpt="Support imaging and pathology customers.",
        )
    )
    assert second_id == job_id
    assert action == "changed"

    row = db.get_job(job_id)
    assert row is not None
    assert row["repost_count"] == 1
    assert row["overall_priority"] == 74.8


def test_unchanged_job_is_not_reversioned(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job = sample_job()
    _, first = db.upsert_job(job)
    _, second = db.upsert_job(job)
    assert first == "new"
    assert second == "unchanged"
    with db.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0]
    assert count == 1
