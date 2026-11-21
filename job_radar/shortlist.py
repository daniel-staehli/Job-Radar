"""Explainable priority for cached employer-feed vacancies. All inputs are local."""
from dataclasses import dataclass
from datetime import timedelta

from . import discovery_match, freshness, work_rights
from .network import STRENGTHS
from .ranking import term_breakdown


RECOMMENDED = "Recommended"


@dataclass
class Recommendation:
    age: str
    rights: work_rights.Assessment
    terms: dict
    contacts: int
    strongest_contact: str
    fit: str
    reasons_to_defer: tuple
    sort_key: tuple

    @property
    def label(self):
        if self.rights.status == work_rights.EXCLUDED:
            return "Ineligible"
        if self.reasons_to_defer:
            return "Review later"
        if self.age == "Date unknown" or self.rights.status == work_rights.VERIFY:
            return "Verify details"
        return RECOMMENDED

    def explanation(self):
        parts = [self.label, *self.reasons_to_defer]
        age = {"Programs": "Program or open application · exempt from the age limit",
               "Date unknown": "Publication date unverified"}.get(self.age, self.age)
        parts.extend([age, self.rights.status])
        if self.terms["total"]:
            parts.append(f"{self.terms['count']}/{self.terms['total']} priority groups mentioned")
        else:
            parts.append("No priority terms set")
        contacts = f"Matching contacts: {self.contacts}"
        if self.strongest_contact:
            contacts += f" (strongest: {self.strongest_contact})"
        parts.extend([self.fit, contacts])
        return " · ".join(parts)


def evaluate(job, *, now, hours, profile, groups, network, review=None, use_review=True):
    age = freshness.category(job, hours, now)
    rights = work_rights.assess(job, profile, today=now.astimezone().date())
    terms = term_breakdown(job, groups)
    fit = discovery_match.label(review)
    active_fit = fit if use_review else ""
    reasons = []
    if job.get("stale"):
        reasons.append("Last feed refresh failed")
    if age == "Older":
        reasons.append(f"Published outside the {hours}-hour window")
    if active_fit == "Poor":
        reasons.append("Saved review found a poor fit")
    if terms["total"] and not terms["count"] and active_fit not in ("Strong", "Plausible"):
        reasons.append("No priority terms matched")
    # Missing, outdated and inconclusive reviews carry neutral weight.
    fit_order = {"Strong": -2, "Plausible": -1, "Stretch": 1, "Poor": 2}
    published = freshness.publication(job)
    recency = now - published if published and published <= now and age != "Date unknown" else timedelta.max
    strength = network.priority(job)
    key = (rights.status == work_rights.EXCLUDED, bool(reasons),
           freshness.FILTERS.index(age), rights.status != work_rights.AUTHORIZED,
           fit_order.get(active_fit, 0), -terms["count"], -strength, recency, job["id"])
    fit_note = f"AI match: {fit}" if use_review else "Saved AI match disabled for ranking"
    return Recommendation(age, rights, terms, network.count(job), STRENGTHS[strength - 1] if strength else "",
                          fit_note, tuple(reasons), key)
