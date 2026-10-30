"""Independent vacancy imports with explicit, optional AI suggestions."""
from __future__ import annotations

from copy import deepcopy
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


class ImportView:
    def _init_import_tasks(self):
        self._import_worker = None
        self._import_job_id = None
        self._import_action = None
        self._import_results = queue.Queue()
        self.import_buttons = []
        self.import_fields = []
        self._paste_window = None
        self.import_ai_evidence = {}
        self.import_unresolved_fields = {}

    def _import_button(self, parent, text, command, *, primary=False):
        button = ttk.Button(parent, text=text, command=command,
                            style="Primary.TButton" if primary else "TButton")
        button.pack(side="left", padx=(0, 8))
        self.import_buttons.append(button)
        self._update_task_controls()
        return button

    def _update_task_controls(self):
        self.action_buttons = [button for button in self.action_buttons if button.winfo_exists()]
        self.import_buttons = [button for button in self.import_buttons if button.winfo_exists()]
        for button in self.action_buttons:
            button.configure(state="disabled" if self._worker is not None else "normal")
        for button in self.import_buttons:
            button.configure(state="disabled" if self._import_worker is not None else "normal")
        self.import_fields = [field for field in self.import_fields if field.winfo_exists()]
        for field in self.import_fields:
            field.configure(state="disabled" if self._import_worker is not None else "normal")
        self._update_generation_control()

    def _run_import(self, label, action, done, failed=None):
        if self._import_worker is not None or self._closed:
            return False
        self._import_started = time.monotonic()
        self._import_label = label
        self.import_task_status.set(label)
        if not self.busy:
            self.progress.start(12)
        self.busy = True
        # The main thread owns all callbacks until Thread.run has released its
        # target. Workers put data into queues and never touch Tk widgets.
        self._import_action = (action, done, failed)
        results = self._import_results
        def worker():
            try:
                results.put((action(), None))
            except Exception as exc:
                results.put((None, exc))
        self._import_worker = threading.Thread(target=worker, daemon=True, name="JobRadarImport")
        self._update_task_controls()
        self._import_worker.start()
        return True

    def _poll_import(self):
        if self._import_worker is None:
            return
        elapsed = int(time.monotonic() - self._import_started)
        label = f"{self._import_label} · elapsed {elapsed // 60}:{elapsed % 60:02d}"
        self.import_task_status.set(label)
        if self._worker is None:
            self.status.set(label)
        if self._import_worker.is_alive():
            return
        self._import_worker.join()
        self._import_worker = None
        self._import_job_id = None
        _, done, failed = self._import_action
        self._import_action = None
        result, error = self._import_results.get_nowait()
        self.busy = self._worker is not None
        self._update_task_controls()
        if not self.busy:
            self.progress.stop()
        if error is not None:
            self.import_task_status.set("Import did not finish. Your form and saved jobs remain available.")
            if failed is not None:
                failed(error)
            self._error(error)
        else:
            try:
                done(result)
                used_ai = isinstance(result, dict) and result.get("model_used") is True
                if used_ai:
                    labels = {"company": "Company", "title": "Position", "location": "Location"}
                    added = [labels.get(name, name) for name in result.get("ai_evidence", {})]
                    missing = [labels.get(name, name) for name in result.get("missing_fields", [])]
                    summary = "AI filled: " + ", ".join(added) + "." if added else "AI check finished. No additional fields filled."
                    summary += " Still to fill: " + ", ".join(missing) + "." if missing else " Review the fields before saving."
                    self.import_task_status.set(summary)
                else:
                    self.import_task_status.set("Import finished. No AI was used.")
            except Exception as exc:
                self.import_task_status.set("Import needs attention. Your saved jobs remain available.")
                self._error(exc)

    def paste_job_text(self):
        if self._import_worker is not None:
            return
        if self._paste_window is not None and self._paste_window.winfo_exists():
            self._paste_window.lift()
            return
        window = tk.Toplevel(self.window)
        self._paste_window = window
        window.title("Paste job text")
        window.geometry("820x600")
        window.minsize(620, 480)
        window.transient(self.window)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Paste the vacancy here", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Local extraction reads labels such as Company: and Job title:. "
                  "Include the job title and workplace as well as the description. Optional AI extraction reads this text in different languages; it does not open the URL. Review the fields before saving.",
                  wraplength=740, style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        ttk.Label(body, textvariable=self.import_ai_notice, wraplength=740,
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(body, text="Vacancy URL (optional)").pack(anchor="w")
        link = ttk.Entry(body)
        link.pack(fill="x", pady=(3, 10))
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        text = ScrolledText(body, wrap="word", font=("Segoe UI", 10))
        text.pack(fill="both", expand=True)
        self.import_fields.extend([text, link])

        def extract(use_ai=False):
            if self._import_worker is not None or self._closed:
                return
            contents, url = text.get("1.0", "end-1c"), link.get().strip()
            if use_ai:
                try:
                    settings = self._selected_ai()
                except Exception as exc:
                    return self._error(exc)
                action = lambda: self.service.import_text_with_ai(
                    contents, source_url=url, provider_settings=settings)
                label = "Extracting missing vacancy details with AI…"
            else:
                action = lambda: self.service.import_text(contents, source_url=url)
                label = "Extracting pasted text locally…"
            def done(result):
                self._fetched(result)
                self.tabs.select(self.import_tab)
                if window.winfo_exists():
                    window.destroy()
            self._run_import(label, action, done)

        self._import_button(buttons, "Extract details (no AI)", extract, primary=True)
        self._import_button(buttons, "Extract with AI", lambda: extract(True))
        self._import_button(buttons, "Close", window.destroy)
        window.protocol("WM_DELETE_WINDOW", lambda: window.destroy() if self._import_worker is None else None)
        text.focus_set()
        self._fit_wrapped_labels(body)
        self.pasted_job_text, self.pasted_job_url, self.extract_pasted_job = text, link, extract
        self.extract_pasted_job_ai = lambda: extract(True)

    def extract_import_with_ai(self):
        """Fill missing review fields without replacing edits or saving a job."""
        if self._import_worker is not None or self._closed:
            return
        try:
            settings = self._selected_ai()
        except Exception as exc:
            return self._error(exc)
        raw = deepcopy(self.draft)
        raw.update({name: variable.get() for name, variable in self.job_fields.items()})
        raw["description"] = self.description.get("1.0", "end-1c")
        known = {name: raw.get(name, "") for name in ("company", "title", "location")}
        candidate = deepcopy(self.discovery_draft)

        def done(result):
            # Extra imported metadata and discovery identity are not AI output.
            # Keep the user's current text and all fields already filled in.
            job = dict(raw)
            for name in ("company", "title", "location", "source_url", "source"):
                if not job.get(name, "").strip():
                    job[name] = result["job"].get(name, "")
            self._fetched({**result, "job": job})
            self.discovery_draft = candidate

        self._run_import(
            "Extracting missing vacancy details with AI…",
            lambda: self.service.import_text_with_ai(
                raw["description"], source_url=raw.get("source_url", "").strip(),
                known_fields=known, provider_settings=settings),
            done,
        )

    def show_import_evidence(self):
        """Show the last extraction's source quotations without calling AI."""
        lines = ["AI SUGGESTIONS FROM THE LAST EXTRACTION", "",
                 "These quotations support the suggested fields. Check their meaning before saving. "
                 "Any later edits you make in the form may differ from these original suggestions.", ""]
        for name, item in self.import_ai_evidence.items():
            label = "Position" if name == "title" else name.capitalize()
            lines.extend((f"{label}: {item['value']}", "Source text:", item["quote"], ""))
        if not self.import_ai_evidence:
            lines.append("There are no AI-suggested fields in this draft. Fields read locally or entered manually do not have AI quotations.")
        if self.import_unresolved_fields:
            lines.extend(("", "UNRESOLVED AT THE LAST EXTRACTION", ""))
            for name, reason in self.import_unresolved_fields.items():
                label = "Position" if name == "title" else name.capitalize()
                lines.extend((f"{label}: {reason}", ""))
        lines.extend(("", "WHAT THIS ACTION READS", "",
                      "AI checks only the supplied vacancy text for Company, Position and Location. It does not open links. "
                      "To read a website, use the top URL field and Fetch details first, then review the fetched description. "
                      "Role type is an optional manual classification. Later form edits do not change this extraction record."))
        self._show_text("Vacancy extraction evidence", "\n".join(lines))
