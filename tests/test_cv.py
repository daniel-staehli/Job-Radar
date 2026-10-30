import json
import shutil
from pathlib import Path

from job_radar.config import ROOT, load_yaml
from job_radar.cv import (
    _normalize_generated_prose,
    compile_latex,
    generate_cvs,
    latex_environment,
    latex_escape,
    materialize_cv_context,
    validate_tailoring,
)
from job_radar.db import JobDatabase
from job_radar.models import SearchJob, TailoredApplication


def job() -> SearchJob:
    """Entirely fictional vacancy used for offline tests."""
    return SearchJob(
        company="Example Laboratory", title="Scientific Solutions Specialist",
        location="Example City", country="Example Country", work_mode="hybrid",
        employment_type="Full time", posted_date="2026-07-28", deadline=None,
        source_url="https://example.org/job/1", apply_url=None,
        description_excerpt="Help laboratory users adopt sample-analysis workflows.",
        salary_text=None, language_requirements=["English"],
        required_years_experience="2 years preferred", domain="Environmental analysis",
        role_family="Scientific solutions", why_fit=["Laboratory analysis", "Scientific communication"],
        hard_gaps=["No commercial software role"], functional_fit=86, hiring_realism=68,
        goal_value=90, evidence=["Laboratory experience required"],
    )


def tailoring() -> TailoredApplication:
    """Synthetic proposal matching examples/master_profile.yaml, never a real CV."""
    return TailoredApplication(
        verdict="apply",
        match_summary="Relevant laboratory experience with a commercial gap.",
        headline_tags=["Environmental research", "Laboratory analysis", "Scientific communication"],
        profile=(
            "Environmental researcher experienced in water-sample analysis, reproducible "
            "laboratory methods, project coordination and scientific communication. "
            "Planned a monitoring study, developed a sample-handling checklist and "
            "coordinated records with a partner laboratory. Uses spreadsheets and "
            "introductory Python scripts to check and analyse measurements."
        ),
        selected_roles=[
            {"role_key": "sample_research", "bullet_ids": [
                "sample_project", "sample_protocol", "sample_collaboration",
                "sample_communication", "sample_methods"]},
            {"role_key": "sample_assistant", "bullet_ids": ["sample_quality", "sample_inventory"]},
        ],
        selected_skill_ids=["skill_communication", "skill_collaboration", "skill_methods", "skill_analysis"],
        selected_publication_ids=["pub_sample_protocol", "pub_sample_monitoring", "pub_sample_quality"],
        selected_additional_ids=["add_sample_workshop", "add_sample_tool"],
        priority_keywords=["laboratory workflows", "adoption", "sample analysis"],
        honest_gaps=["No prior scientific-software commercial role."],
        interview_angles=["Explain a laboratory handover and its documentation."],
        cv_risks_to_review=[],
        motivation_letter={
            "language": "English", "recipient_name": None, "recipient_role": None,
            "salutation": "Dear Hiring Team,",
            "subject": "Application for Scientific Solutions Specialist",
            "paragraphs": [
                (
                    "I am applying for the Scientific Solutions Specialist position because "
                    "it combines laboratory work with support for scientific users. My "
                    "environmental research experience has involved planning sample "
                    "collection, checking measurements and explaining procedures. I would "
                    "like to apply these skills in a team helping laboratories introduce "
                    "reliable analysis workflows."
                ),
                (
                    "At Example Institute, I planned a water-monitoring study and "
                    "documented the sampling schedule. I developed a sample-handling "
                    "checklist and used spreadsheets and introductory Python scripts to "
                    "analyse example measurements. Preparing clear records helped the "
                    "team understand which steps were complete and which measurements "
                    "required a second check."
                ),
                (
                    "I also coordinated sample records with a partner laboratory and "
                    "prepared onboarding notes for new team members. These activities "
                    "required careful handovers and an ability to describe a procedure "
                    "in practical terms. Earlier work in a teaching laboratory involved "
                    "checking units, identifying missing values and keeping an inventory "
                    "of materials ready for classes."
                ),
                (
                    "I have not yet held a commercial scientific-software position. "
                    "I would bring experience documenting laboratory processes and "
                    "communicating research updates, together with a willingness to "
                    "learn the product and customer responsibilities. I would welcome "
                    "the opportunity to discuss how this background could help your "
                    "scientific users and support the priorities of the team."
                ),
            ],
            "closing": "Kind regards,",
        },
    )


def test_tailoring_validates_and_materializes():
    profile = load_yaml(ROOT / "examples" / "master_profile.yaml")
    result = tailoring()
    assert validate_tailoring(result, profile) == []
    context = materialize_cv_context(result, profile)
    assert context["experiences"][0]["bullets"][0]["id"] == "sample_project"
    assert len(context["skills"]) == 4


def test_forbidden_claim_is_blocked():
    profile = load_yaml(ROOT / "examples" / "master_profile.yaml")
    result = tailoring().model_copy(
        update={"profile": "Software engineer and environmental researcher."}
    )
    errors = validate_tailoring(result, profile)
    assert any("software engineer" in error for error in errors)


def test_public_cv_template_uses_only_supplied_additional_information():
    profile = load_yaml(ROOT / "examples" / "master_profile.yaml")
    environment = latex_environment(ROOT / "templates")
    environment.filters["tex"] = latex_escape
    template = environment.get_template("cv_default.tex.j2")
    profile["contact"]["additional_information"] = "Available from January & open to travel."
    rendered = template.render(**materialize_cv_context(tailoring(), profile))
    assert r"Available from January \& open to travel." in rendered

    del profile["contact"]["additional_information"]
    rendered = template.render(**materialize_cv_context(tailoring(), profile))
    assert r"\cvsection{Additional information}" not in rendered
    assert "citizen" not in rendered.casefold()


def test_generated_prose_is_normalized_before_validation():
    result = tailoring()
    letter = result.motivation_letter.model_copy(
        update={
            "language": "German",
            "subject": "Bewerbung — Scientific Solutions Specialist",
            "paragraphs": [
                paragraph.replace("position", "Straße – position", 1)
                for paragraph in result.motivation_letter.paragraphs
            ],
        }
    )
    result = result.model_copy(
        update={
            "profile": result.profile + " — Research profile.",
            "motivation_letter": letter,
        }
    )

    normalized = _normalize_generated_prose(result)
    generated = " ".join(
        [
            normalized.profile,
            normalized.motivation_letter.subject,
            *normalized.motivation_letter.paragraphs,
        ]
    )
    assert "—" not in generated
    assert "–" not in generated
    assert "ß" not in generated


def test_generate_cv_uses_one_response_per_job(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, _ = db.upsert_job(job())
    selection = tmp_path / "selection.yaml"
    selection.write_text(f"job_ids:\n  - {job_id}\nurls: []\n", encoding="utf-8")
    calls = []

    def fake_response(**kwargs):
        calls.append(kwargs)
        return tailoring(), {"output": []}

    outputs = generate_cvs(
        db=db,
        selection_path=selection,
        profile_path=ROOT / "examples" / "master_profile.yaml",
        template_path=ROOT / "templates" / "cv_default.tex.j2",
        output_root=tmp_path / "applications",
        dry_run=False,
        compile_pdf=False,
        response_fn=fake_response,
        page_fetch_fn=lambda url, fallback: (fallback, "test cache"),
    )
    assert len(calls) == 1
    assert len(outputs) == 1
    assert (outputs[0] / "cv.tex").exists()
    assert (outputs[0] / "motivation_letter.tex").exists()
    assert (outputs[0] / "fit_analysis.md").exists()
    saved = json.loads((outputs[0] / "tailoring.json").read_text())
    assert saved["verdict"] == "apply"


def test_generate_cv_resume_reuses_saved_response(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, _ = db.upsert_job(job())
    selection = tmp_path / "selection.yaml"
    selection.write_text(f"job_ids:\n  - {job_id}\nurls: []\n", encoding="utf-8")
    arguments = {
        "db": db,
        "selection_path": selection,
        "profile_path": ROOT / "examples" / "master_profile.yaml",
        "template_path": ROOT / "templates" / "cv_default.tex.j2",
        "output_root": tmp_path / "applications",
        "dry_run": False,
        "compile_pdf": False,
        "page_fetch_fn": lambda url, fallback: (fallback, "test cache"),
    }
    outputs = generate_cvs(
        **arguments,
        response_fn=lambda **kwargs: (tailoring(), {"output": []}),
    )
    (outputs[0] / "cv.tex").unlink()
    (outputs[0] / "motivation_letter.tex").unlink()

    def unexpected_response(**kwargs):
        raise AssertionError("Resume should not make another model request.")

    resumed = generate_cvs(
        **arguments,
        resume=True,
        response_fn=unexpected_response,
    )
    assert (resumed[0] / "cv.tex").exists()
    assert (resumed[0] / "motivation_letter.tex").exists()


def test_distinct_same_title_postings_keep_separate_dossiers(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    first = "job_ext_" + "1" * 24
    second = "job_ext_" + "2" * 24
    db.upsert_job(job(), job_id=first)
    db.upsert_job(job().model_copy(update={"source_url": "https://example.org/job/2"}), job_id=second)
    outputs = generate_cvs(db=db, selection_path=tmp_path / "unused.yaml",
        profile_path=ROOT / "examples/master_profile.yaml", template_path=ROOT / "templates/cv_default.tex.j2",
        output_root=tmp_path / "applications", dry_run=False, compile_pdf=False,
        job_ids_override=[first, second], offline=True,
        response_fn=lambda **kwargs: (tailoring(), {"output": []}))
    assert len(outputs) == len(set(outputs)) == 2
    for directory, expected in zip(outputs, [first, second]):
        assert (directory / "cv.tex").is_file()
        assert json.loads((directory / "generation_inputs.json").read_text())["job_id"] == expected
        assert json.loads((directory / "generation_context.json").read_text())["job_id"] == expected


def test_old_single_job_output_directory_is_kept_for_resume(tmp_path):
    from job_radar.cv import _application_directory
    from job_radar.normalize import slugify
    payload = job().model_dump()
    legacy = tmp_path / slugify(f"{payload['company']}-{payload['title']}")
    legacy.mkdir()
    identifier = "job_old"
    (legacy / "generation_inputs.json").write_text(json.dumps({"job_id": identifier}), encoding="utf-8")
    assert _application_directory(tmp_path, payload, identifier) == legacy
    assert _application_directory(tmp_path, payload, "job_other") != legacy
    assert json.loads((legacy / "generation_inputs.json").read_text())["job_id"] == identifier


def test_rendered_cv_compiles_when_latex_is_available(tmp_path):
    if not shutil.which("latexmk"):
        return
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, _ = db.upsert_job(job())
    selection = tmp_path / "selection.yaml"
    selection.write_text(f"job_ids:\n  - {job_id}\nurls: []\n", encoding="utf-8")

    outputs = generate_cvs(
        db=db,
        selection_path=selection,
        profile_path=ROOT / "examples" / "master_profile.yaml",
        template_path=ROOT / "templates" / "cv_default.tex.j2",
        output_root=tmp_path / "applications",
        dry_run=False,
        compile_pdf=False,
        response_fn=lambda **kwargs: (tailoring(), {"output": []}),
        page_fetch_fn=lambda url, fallback: (fallback, "test cache"),
    )
    ok, log = compile_latex(outputs[0])
    assert ok, log
    assert (outputs[0] / "cv.pdf").exists()


def test_letter_layout_places_contact_before_subject_and_body():
    from job_radar.cv import materialize_letter_context
    env = latex_environment(ROOT / "templates")
    env.filters["tex"] = latex_escape
    proposal = tailoring()
    proposal.motivation_letter.subject = "Application for Research & Development Specialist"
    profile = load_yaml(ROOT / "examples" / "master_profile.yaml")
    context = materialize_letter_context(proposal, profile, job().model_dump())
    tex = env.get_template("motivation_letter.tex.j2").render(**context)
    assert tex.index("mailto:") < tex.index("Application for Research") < tex.index("Dear Hiring Team")
    assert r"Research \& Development" in tex
    assert tex.index("Dear Hiring Team") < tex.index(latex_escape(proposal.motivation_letter.closing))
    assert r"\opening" not in tex  # The old letter class inserted addresses after the subject.
    assert "None" not in tex
