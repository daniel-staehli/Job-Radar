"""Local, conservative screening of the candidate's own work rights."""
from dataclasses import dataclass
from datetime import date
import json
import re

from .profile import _atomic_bytes, _data_dir, _file_bytes, _fingerprint, _profile_lock

COUNTRIES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden", "CH": "Switzerland", "IS": "Iceland", "LI": "Liechtenstein",
    "NO": "Norway", "GB": "United Kingdom", "US": "United States", "SG": "Singapore",
    "AU": "Australia", "NZ": "New Zealand", "QA": "Qatar",
}
FREE_MOVEMENT = set(COUNTRIES) - {"GB", "US", "SG", "AU", "NZ", "QA"}
NAMES = {name.casefold(): code for code, name in COUNTRIES.items()}
NAMES.update({"uk": "GB", "u.k.": "GB", "usa": "US", "u.s.": "US", "u.s.a.": "US",
              "united states of america": "US", "schweiz": "CH", "suisse": "CH", "svizzera": "CH",
              "deutschland": "DE", "czech republic": "CZ", "england": "GB", "scotland": "GB",
              "wales": "GB", "northern ireland": "GB"})
CITIES = {"zürich": "CH", "zurich": "CH", "basel": "CH", "lausanne": "CH", "genève": "CH",
          "bern": "CH", "doha": "QA", "auckland": "NZ"}
PREFERENCES = ("Undecided", "Open to sponsorship", "Prefer roles without sponsorship")
STATUSES = ("Not confirmed", "Can work for any employer", "Conditions apply", "Require employer sponsorship")
VERIFY = "Needs verification"
EXCLUDED = "Confirmed ineligible"
AUTHORIZED = "Authorized"
FILE = "work_authorization.json"


def country(value):
    value = str(value or "").strip()
    if value.upper() in COUNTRIES:
        return value.upper()
    return NAMES.get(value.casefold())


def validate(value):
    if (not isinstance(value, dict) or value.get("version") != 1 or
            set(value) != {"version", "citizenships", "rights", "preference"}):
        raise ValueError("Unsupported work authorization profile.")
    citizens = value.get("citizenships")
    rights = value.get("rights")
    if (not isinstance(citizens, list) or any(not isinstance(c, str) or not re.fullmatch("[A-Z]{2}", c) for c in citizens)
            or value.get("preference") not in PREFERENCES or not isinstance(rights, dict)):
        raise ValueError("Check the citizenship codes and sponsorship preference.")
    for code, row in rights.items():
        if (code not in COUNTRIES or not isinstance(row, dict) or row.get("status") not in STATUSES
                or set(row) != {"status", "permit", "visa", "expires"}):
            raise ValueError("Choose a supported work country and status.")
        for key in ("permit", "visa", "expires"):
            if not isinstance(row.get(key), str) or len(row[key]) > 160:
                raise ValueError("Use short permit and visa labels and an optional expiry date.")
        if row["expires"]:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["expires"]):
                raise ValueError("Use YYYY-MM-DD for the expiry date.")
            try:
                date.fromisoformat(row["expires"])
            except ValueError:
                raise ValueError(f"Check the expiry date for {COUNTRIES[code]}.") from None
    return value


def empty():
    return {"version": 1, "citizenships": [], "rights": {}, "preference": PREFERENCES[0]}


def load(root):
    raw = _file_bytes(_data_dir(root) / FILE, 64000)
    return (validate(json.loads(raw)) if raw is not None else empty()), _fingerprint(raw)


def save(root, value, fingerprint):
    raw = json.dumps(validate(value), ensure_ascii=False, indent=2).encode()
    if len(raw) > 64000:
        raise ValueError("The work authorization profile is too large.")
    data = _data_dir(root)
    with _profile_lock(data):
        if load(root)[1] != fingerprint:
            raise ValueError("These settings changed in another window. Reopen them before saving.")
        _atomic_bytes(data / FILE, raw)


# Restrict automatic exclusion to complete, unconditional statements. Preserve
# other wording as evidence for the user, including questions and exceptions.
_SIGNAL = re.compile(r"sponsor(?:ship|ed|ing)?\b|\b(?:visas?|immigration|citizenship|citizens? only|nationals? only|clearance|work permits?|work authori[sz]ation|right to work|authori[sz]ed to work|permanent residents?)\b|arbeitserlaubnis|arbeitsbewilligung|permis de travail|parrainage de visa", re.I)
_NO = re.compile(r"(?:no (?:visa |employment )?sponsorship(?: (?:is )?(?:available|provided|offered))?|"
                 r"(?:visa |employment )?sponsorship (?:is (?:not available|not offered|unavailable|not provided))|"
                 r"we (?:do not|cannot|are unable to) (?:offer|provide) (?:visa |employment )?sponsorship|"
                 r"we (?:cannot|do not|are unable to) sponsor (?:work )?visas|"
                 r"kein (?:visa[- ]?)?sponsoring(?: möglich)?|aucun parrainage de visa(?: disponible)?)"
                 r"(?: for this (?:role|position))?", re.I)
_SPECIAL = re.compile(r"citizenship|\bcitizens?\b|\bnationals?\b|clearance|permanent residents?|residen(?:ce|cy) requirement|must (?:reside|live|be resident)|staats(?:angehörigkeit|bürgerschaft)|nationalité|bewilligung erforderlich|permis de travail (?:valide )?(?:exigé|obligatoire)", re.I)
_WORK_IN = re.compile(r"\b(?:work(?:ing)? in|work authori[sz]ation (?:in|for)) (?:the )?("
                      + "|".join(map(re.escape, sorted(set(NAMES) | {"us"}, key=len, reverse=True)))
                      + r")(?!\w)", re.I)


def _destination(job):
    place = (job.get("location") or "").strip()
    explicit = country(job.get("country"))
    if job.get("country") and not explicit:
        return None
    if job.get("secondary_locations") or any(str(e).startswith("Other published locations:") for e in job.get("evidence") or []):
        return None
    if re.search(r"[/;|&]|\b(?:or|and)\b|worldwide|anywhere|\bEMEA\b|\bEurope\b", place, re.I):
        return None
    parts = [part.strip() for part in re.split(r"[,()]", place) if part.strip()]
    # US state abbreviations can also be country codes (e.g. Dover, DE).
    # An explicit country name or structured country resolves this ambiguity.
    state_codes = {"DE", "MT"}  # US postal abbreviations also supported as work countries
    if len(parts) > 1:
        parts = [part for part in parts if part.upper() not in state_codes]
    codes = {code for part in parts if (code := country(part))}
    if len(parts) > 1 and not explicit and codes and not country(parts[-1]) and parts[-1].casefold() not in {"remote", "hybrid", "onsite"}:
        return None
    if explicit:
        codes.add(explicit)
    if len(codes) == 1:
        return codes.pop()
    return CITIES.get(place.casefold()) if not codes and not job.get("country") else None


@dataclass(frozen=True)
class Assessment:
    status: str
    reason: str
    evidence: tuple
    destination: str | None

    def details(self):
        lines = [f"Work authorization · {self.status}", self.reason]
        if self.evidence:
            lines += ["Employer wording:", *self.evidence]
        return "\n".join(lines)


def assess(job, profile, today=None):
    code = _destination(job)
    text = job.get("description") or job.get("description_excerpt") or ""
    chunks = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    evidence = tuple(dict.fromkeys(s.strip() for s in chunks if _SIGNAL.search(s) or _SPECIAL.search(s)))
    def result(status, reason):
        return Assessment(status, reason, evidence, code)
    if not code:
        return result(VERIFY, "Confirm the work country. The listing has an unclear, alternative or unsupported location.")
    name = COUNTRIES[code]
    if any(country(match) != code for s in evidence for match in _WORK_IN.findall(s)):
        return result(VERIFY, f"The work-rights wording mentions another country. Confirm which rules apply to this {name} posting.")
    citizens = profile["citizenships"]
    row = profile["rights"].get(code, {})
    entitlement = row.get("status")
    expired = bool(row.get("expires") and date.fromisoformat(row["expires"]) < (today or date.today()))
    # Only exact denials qualify, and other sponsorship wording may qualify or
    # contradict them. An affirmative visa answer is never inferred.
    sponsor_lines = [s for s in evidence if re.search("sponsor|parrainage", s, re.I)]
    denied = bool(sponsor_lines) and all(_NO.fullmatch(s.strip(" .\t•*-")) for s in sponsor_lines)
    specific_document = re.search(r"(?:must (?:already )?(?:hold|have|possess)|requires? (?:an? |a current |a valid ))[^.!?\n]{0,60}\b(?:visa|permit|green card)\b", text, re.I)
    if any(_SPECIAL.search(s) for s in evidence) or specific_document:
        return result(VERIFY, f"Check the employer’s specific document, citizenship, residence or clearance wording for {name}.")
    if code in citizens and entitlement == STATUSES[3]:
        return result(VERIFY, f"Your citizenship and sponsorship entries for {name} conflict. Review your profile.")
    if code in citizens:
        return result(AUTHORIZED, f"Your profile records citizenship in {name}.")
    if entitlement == STATUSES[1] and not expired:
        return result(AUTHORIZED, f"You confirmed current permission to work for any employer in {name}.")
    movement = (code in FREE_MOVEMENT - {"LI"} and bool(set(citizens) & FREE_MOVEMENT))
    common_travel = (code == "GB" and "IE" in citizens) or (code == "IE" and "GB" in citizens)
    if movement or common_travel:
        if entitlement == STATUSES[3]:
            return result(VERIFY, f"Your sponsorship entry conflicts with a citizenship-based work route in {name}.")
        return result(AUTHORIZED, f"Your citizenship provides a general work route in {name}. Check any registration or permit formalities before starting.")
    exception = re.search(r"\b(?:except(?:ion)?s?|unless|case.by.case|waivers?|exemptions?|subject to|ausnahmen?|sauf)\b", text, re.I)
    if denied and exception:
        return result(VERIFY, f"Check whether an exception to the sponsorship policy applies in {name}.")
    unclear_policy = any(s not in sponsor_lines and re.search(r"\b(?:visas?|immigration)\b", s, re.I) for s in evidence)
    if entitlement == STATUSES[3] and denied and not unclear_policy and not expired and not job.get("stale"):
        return result(EXCLUDED, f"You confirmed you require employer sponsorship in {name}. The listing explicitly rules it out.")
    if expired:
        reason = "The expiry date recorded in your profile has passed."
    elif entitlement == STATUSES[2]:
        reason = "Check whether your recorded permit or visa covers this employer, role and hours."
    elif (code == "AU" and "NZ" in citizens) or (code == "NZ" and "AU" in citizens):
        reason = "A trans-Tasman visa route may apply. Confirm your entry and work status."
    elif code == "LI":
        reason = "Check residence or cross-border work conditions in Liechtenstein."
    elif denied:
        reason = "The employer rules out sponsorship. Confirm your existing right to work."
    else:
        reason = {"US": "Check employment authorization for this employer.",
                  "GB": "Confirm your UK immigration status and right to work.",
                  "SG": "Confirm a qualifying work pass or exemption.",
                  "AU": "Check the work conditions on your Australian visa.",
                  "NZ": "Check the work conditions on your New Zealand visa.",
                  "QA": "Confirm the applicable work permit or employer arrangement."}.get(code, "Confirm the applicable permission or permit.")
    if profile["preference"] == PREFERENCES[2] and entitlement == STATUSES[3]:
        reason += " This role may conflict with your preference to avoid sponsorship."
    return result(VERIFY, f"{name}: {reason}")
