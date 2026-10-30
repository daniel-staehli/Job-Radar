"""On-demand, saved job-match reviews using the current AI provider."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import job_match
from .providers import describe


class MatchView:
    def open_job_match(self):
        if not self.selected_job:
            return self._error(ValueError("Select a saved job first."))
        if not self._flush_tracking(report_error=True):
            return
        job = deepcopy(self.selected_job)
        window = tk.Toplevel(self.window)
        window.title("Job match review")
        window.geometry("900x680")
        window.minsize(680, 480)
        window.transient(self.window)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{job.get('company', '')} — {job.get('title', '')}",
                  style="Heading.TLabel", wraplength=820).pack(anchor="w")
        notice = tk.StringVar(value="")
        ttk.Label(body, textvariable=notice, wraplength=820, style="Muted.TLabel").pack(anchor="w", pady=(8, 10))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(0, 10))
        viewer = ScrolledText(body, wrap="word", font=("Segoe UI", 10), padx=12, pady=12)
        viewer.pack(fill="both", expand=True)

        def display(text):
            if not window.winfo_exists():
                return
            viewer.configure(state="normal")
            viewer.delete("1.0", "end")
            viewer.insert("1.0", text)
            viewer.configure(state="disabled")

        def current_job():
            return next((item for item in self.service.jobs() if item['job_id'] == job['job_id']), job)

        def load_saved():
            if not window.winfo_exists():
                return
            try:
                package = job_match.latest_assessment(self.service.root, current_job())
                display(job_match.format_assessment(package) if package else
                        "No match review saved yet.\n\nRun an AI review to compare the saved vacancy with your saved profile. "
                        "The review explains strengths, evidence gaps and uncertainties. "
                        "It is guidance for your own decision, not a hiring prediction.")
            except Exception as exc:
                display("The saved review could not be read: " + str(exc))
            try:
                provider = describe(self._selected_ai())
                notice.set("Run AI review sends your saved profile and this vacancy to " + provider +
                           ". It uses the selected provider's allowance. Opening a saved review stays local.")
            except ValueError as exc:
                notice.set(str(exc))

        def review():
            if self.busy:
                return
            if self._profile_has_unsaved_changes():
                self.tabs.select(self.profile_tab)
                return self._error(ValueError("Save your changes in My profile before assessing a job."))
            try:
                settings = self._selected_ai()
                selected = deepcopy(current_job())
            except Exception as exc:
                return self._error(exc)
            notice.set("Reviewing the saved profile and vacancy. This may take several minutes.")

            def action():
                self._require_provider(settings)
                return job_match.assess_job(self.service.root, selected, settings)

            def done(result):
                # Reload to flag a profile or vacancy changed during the request.
                load_saved()
                self.status.set("Job match review saved. No application documents were created.")

            def failed(error):
                if window.winfo_exists():
                    notice.set("Review did not finish. The earlier saved review is unchanged. " + str(error))

            self._run("Assessing this job against your saved profile…", action, done, failed)

        self._button(actions, "Run AI review", review, primary=True)
        self._button(actions, "Reload saved review", load_saved)
        self._button(actions, "My profile", lambda: self.tabs.select(self.profile_tab))
        load_saved()
        # Kept for UI checks and integration without searching widget text.
        self.match_window = window
        self.match_viewer = viewer
        self.match_notice = notice
        self._run_match_review = review
