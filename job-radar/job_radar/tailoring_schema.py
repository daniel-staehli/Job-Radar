"""Bind application selections to the verified profile before requesting AI output.

This narrows the existing document schema; it does not alter verified content or
replace the pipeline's fact checks. Each role's bullet IDs stay paired with that
role instead of allowing a cross-role mix of valid identifiers.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import Field, create_model

from .models import SelectedRole, TailoredApplication


def _identifiers(items, *, field: str | None = None) -> tuple[str, ...]:
    if not isinstance(items, list):
        raise ValueError("Profile selections must be lists of verified identifiers.")
    values = []
    for item in items:
        if field is not None and not isinstance(item, dict):
            raise ValueError("Verified profile entries must be mappings with text identifiers.")
        value = item.get(field) if field is not None else item
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Profile selection identifiers must be non-empty text.")
        if value not in values:
            values.append(value)
    return tuple(values)


def _selection(values: tuple[str, ...], description: str):
    if not values:
        return list[str], Field(max_length=0, description=description)
    return list[Literal[values]], Field(description=description)


def application_schema(profile: dict) -> type[TailoredApplication]:
    """Return a fresh, deterministic Pydantic schema for this verified profile."""
    if not isinstance(profile, dict):
        raise ValueError("The verified profile must be a mapping.")
    experiences = profile.get("experiences", [])
    role_keys = _identifiers(experiences, field="key")
    if len(role_keys) != len(experiences):
        raise ValueError("Verified experience keys must be unique.")
    role_models = []
    for index, role in enumerate(experiences, start=1):
        bullets = _identifiers(role.get("bullets", []), field="id")
        role_models.append(create_model(
            f"ProfileRole{index}", __base__=SelectedRole,
            role_key=(Literal[(role["key"],)], Field(
                description="Exact key of this verified past/current experience, never the vacancy title.")),
            bullet_ids=_selection(bullets, "Choose only verified bullet IDs belonging to this experience."),
        ))
    if role_models:
        role_type = role_models[0] if len(role_models) == 1 else Union[tuple(role_models)]
        roles_field = (list[role_type], Field(description="Select verified experiences using their exact key and matching bullet IDs."))
    else:
        roles_field = (list[SelectedRole], Field(max_length=0, description="No verified experiences are available; return an empty list."))
    return create_model(
        "ProfileTailoredApplication", __base__=TailoredApplication,
        selected_roles=roles_field,
        headline_tags=_selection(_identifiers(profile.get("allowed_headline_tags", [])),
                                 "Select only supplied headline tags."),
        selected_skill_ids=_selection(_identifiers(profile.get("skills", []), field="id"),
                                      "Select only supplied skill IDs."),
        selected_publication_ids=_selection(_identifiers(profile.get("publications", []), field="id"),
                                            "Select only supplied publication IDs."),
        selected_additional_ids=_selection(_identifiers(profile.get("additional_experience", []), field="id"),
                                           "Select only supplied additional-experience IDs."),
    )
