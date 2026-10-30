from job_radar.normalize import (
    canonicalize_url,
    identity_key,
    slugify,
    stable_job_id,
)


def test_canonicalize_url_removes_tracking_and_fragment():
    url = (
        "https://Example.com/jobs/123/?utm_source=linkedin&gh_src=abc"
        "&keep=yes#apply"
    )
    assert canonicalize_url(url) == "https://example.com/jobs/123?keep=yes"


def test_identity_is_case_and_diacritic_insensitive():
    left = identity_key("Éxample Lab", "Medical Science Liaison", "Zürich")
    right = identity_key("EXAMPLE LAB", "medical-science liaison", "Zurich")
    assert left == right
    assert stable_job_id("A", "B", "C") == stable_job_id("a", "b", "c")


def test_slugify_is_filesystem_friendly():
    assert slugify("Example — Sample Analysis / Global") == "example-sample-analysis-global"

