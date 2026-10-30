from job_radar.config import ROOT
from job_radar.db import JobDatabase
from job_radar.search import run_search


def test_search_dry_run_creates_distinct_prompts(tmp_path):
    totals = run_search(
        db=JobDatabase(tmp_path / "jobs.sqlite3"),
        profile_path=ROOT / "examples" / "master_profile.yaml",
        strategies_path=ROOT / "examples" / "search_strategies.yaml",
        mode="initial",
        passes=3,
        since_days=7,
        max_results=10,
        dry_run=True,
        prompt_dir=tmp_path / "prompts",
    )
    assert totals["passes"] == 3
    prompts = sorted((tmp_path / "prompts").glob("*.json"))
    assert len(prompts) == 3
    assert len({path.name for path in prompts}) == 3

