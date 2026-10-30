from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchJob(StrictModel):
    company: str
    title: str
    location: str
    country: str | None
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    employment_type: str | None
    posted_date: str | None
    deadline: str | None
    source_url: str
    apply_url: str | None
    description_excerpt: str
    salary_text: str | None
    language_requirements: list[str]
    required_years_experience: str | None
    domain: str
    role_family: str
    why_fit: list[str]
    hard_gaps: list[str]
    functional_fit: int = Field(ge=0, le=100)
    hiring_realism: int = Field(ge=0, le=100)
    goal_value: int = Field(ge=0, le=100)
    evidence: list[str]


class SearchBatch(StrictModel):
    jobs: list[SearchJob]
    search_notes: list[str]


class SelectedRole(StrictModel):
    role_key: str
    bullet_ids: list[str]


class MotivationLetter(StrictModel):
    language: Literal["English", "German", "French"]
    recipient_name: str | None
    recipient_role: str | None
    show_recipient_block: bool = Field(
        default=True,
        description="Show the company/recipient header block. Set false to hide this block without changing letter wording.",
    )
    salutation: str
    subject: str
    paragraphs: list[str] = Field(min_length=3, max_length=6)
    closing: str


class TailoredApplication(StrictModel):
    verdict: Literal["apply", "consider", "skip"]
    match_summary: str
    headline_tags: list[str]
    profile: str
    selected_roles: list[SelectedRole]
    selected_skill_ids: list[str]
    selected_publication_ids: list[str]
    selected_additional_ids: list[str]
    priority_keywords: list[str]
    honest_gaps: list[str]
    interview_angles: list[str]
    cv_risks_to_review: list[str]
    motivation_letter: MotivationLetter
