"""Profile dialog for optional local work-authorization preferences."""
from copy import deepcopy
import re
import tkinter as tk
from tkinter import ttk

from . import work_rights as rights


class WorkRightsDialog:
    def __init__(self, app, profile, fingerprint):
        self.app, self.fingerprint = app, fingerprint
        self.value = deepcopy(profile)
        self.window = tk.Toplevel(app.window)
        self.window.title("Work authorization")
        self.window.transient(app.window)
        self.window.grab_set()
        panel = ttk.Frame(self.window, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="Saved on this computer for vacancy screening. Enter document types only. "
                  "Leave out ID numbers and scans.", wraplength=570).grid(row=0, columnspan=2, sticky="w", pady=(0, 10))
        self.citizens = app._field(panel, "Citizenships (comma-separated)", 1)
        self.citizens.set(", ".join(rights.COUNTRIES.get(c, c) for c in profile["citizenships"]))
        ttk.Label(panel, text="Use country names or two-letter codes. GB means British citizen.").grid(
            row=2, columnspan=2, sticky="w")
        self.preference = app._field(panel, "Sponsorship preference", 3, rights.PREFERENCES)
        self.preference.set(profile["preference"])
        ttk.Separator(panel).grid(row=4, columnspan=2, sticky="ew", pady=12)
        self.destination = app._field(panel, "Work country", 5, sorted(rights.COUNTRIES.values()))
        self.country_code = None
        self.fields = {key: app._field(panel, label, index, choices) for index, (key, label, choices) in
                       enumerate((("permit", "Current permit type", None), ("visa", "Existing visa type", None),
                                  ("expires", "Permission expires (YYYY-MM-DD)", None),
                                  ("status", "My current work rights there", rights.STATUSES)), start=6)}
        self.destination.trace_add("write", self.switch_country)
        country_code = next(iter(profile["rights"]), None)
        self.destination.set(rights.COUNTRIES.get(country_code, ""))
        self.notice = tk.StringVar(value="Choose each country you want to record. Save applies all countries together.")
        ttk.Label(panel, textvariable=self.notice, wraplength=580).grid(row=10, columnspan=2, sticky="w", pady=10)
        actions = ttk.Frame(panel)
        actions.grid(row=11, columnspan=2, sticky="ew")
        ttk.Button(actions, text="Save work authorization", command=self.save).pack(side="left")
        ttk.Button(actions, text="Clear selected country", command=self.clear_country).pack(side="left", padx=6)
        ttk.Button(actions, text="Cancel", command=self.window.destroy).pack(side="right")

    def clear_country(self):
        for key, field in self.fields.items():
            field.set(rights.STATUSES[0] if key == "status" else "")

    def stage_country(self):
        if self.country_code:
            row = {key: field.get().strip() for key, field in self.fields.items()}
            if row["status"] == rights.STATUSES[0] and not any(row[k] for k in ("permit", "visa", "expires")):
                self.value["rights"].pop(self.country_code, None)
            else:
                self.value["rights"][self.country_code] = row

    def switch_country(self, *_):
        self.stage_country()
        self.country_code = rights.country(self.destination.get())
        self.clear_country()
        for key, value in self.value["rights"].get(self.country_code, {}).items():
            self.fields[key].set(value)

    def save(self):
        try:
            self.stage_country()
            if not self.country_code and (self.fields["status"].get() != rights.STATUSES[0] or
                                         any(self.fields[k].get().strip() for k in ("permit", "visa", "expires"))):
                raise ValueError("Choose the country for this permit or visa.")
            codes = []
            for token in filter(None, (s.strip() for s in self.citizens.get().split(","))):
                code = rights.country(token) or token.upper()
                if not re.fullmatch("[A-Z]{2}", code):
                    raise ValueError("Enter a country name or its two-letter code for each citizenship.")
                if code not in codes:
                    codes.append(code)
            self.value.update(citizenships=codes, preference=self.preference.get())
            rights.save(self.app.service.root, self.value, self.fingerprint)
        except (ValueError, OSError) as exc:
            self.notice.set(str(exc))
            return
        self.app.rights_profile = self.value
        self.app._fill_candidates()
        if self.app.selected_job:
            self.app.job_rights.set("Work authorization · " + rights.assess(self.app.selected_job, self.value).status)
        self.app.profile_notice.set("Work authorization saved locally. Vacancy screening updated.")
        self.window.destroy()
