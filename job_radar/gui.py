"""Small native desktop interface over Job Radar's existing workflows."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .gui_services import GuiService
from .gui_theme import apply_theme
from .gui_discovery import DiscoveryView
from .gui_ai import AiSettingsView
from .gui_job_context import JobContextView
from .gui_manual_edit import ManualEditView
from .gui_word_export import WordExportView
from .gui_profile import ProfileView
from .gui_match import MatchView
from .gui_import import ImportView
from .normalize import stable_job_id
from .providers import PROVIDER_LABELS
from .config import local_today
from .cancellation import CancellationToken, GenerationCancelled, cancellation_scope
from .generation_state import finish_outputs, mark_incomplete
from .tracking import STATUSES


class JobRadarApp(DiscoveryView, AiSettingsView, JobContextView, ManualEditView, ProfileView, MatchView, ImportView, WordExportView):
    def __init__(self, window: tk.Tk, service: GuiService | None = None):
        self.window = window
        self.service = service or GuiService()
        self.busy = False
        self.results = queue.Queue()
        self._worker = None
        self._active_action = None
        self._poll_timer = None
        self._closed = False
        self.action_buttons = []
        self._init_import_tasks()
        self._generation_job_id = None
        self._generation_cancel = None
        self.jobs = []
        self.references = []
        self.chosen_reference_ids = []
        self.available_reference_ids = []
        self.selected_job = None
        self._tracking_loading = False
        self._tracking_timer = None
        self._filter_loading = False
        self._last_job_filters = ('', 'All statuses')
        self.draft = {}
        self.draft_path = None
        self.discovery_draft = None
        self.last_output = None
        self.saved_drafts = []
        self.window.title(f"Job Radar {__version__}")
        width = min(1180, self.window.winfo_screenwidth() - 60)
        height = min(820, self.window.winfo_screenheight() - 100)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(960, 680)
        self.style = apply_theme(window)
        frame = ttk.Frame(window, padding=18, style="Shell.TFrame")
        frame.pack(fill="both", expand=True)
        brand = ttk.Frame(frame, padding=(18, 12), style="Brand.TFrame")
        brand.pack(fill="x", pady=(0, 14))
        self._button(brand, "AI settings", lambda: self.open_ai_settings(), side="right").configure(style="Brand.TButton")
        ttk.Label(brand, text="Job Radar", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, text="Your next role, one clear step at a time.", style="BrandSub.TLabel").pack(anchor="w", pady=(2, 0))
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x", before=self.tabs)
        self.jobs_tab = self._tab("Jobs")
        self.find_tab = self._tab("Find jobs")
        self.import_tab = self._tab("Add a job")
        self.refs_tab = self._tab("Reference library")
        self.profile_tab = self._tab("My profile")
        self.docs_tab = self._tab("Create documents")
        self.review_tab = self._tab("Review & improve")
        self._build_jobs()
        self._build_discovery()
        self._build_import()
        self._build_references()
        self._build_documents()
        self._build_review()
        self._build_profile()
        self._update_ai_labels()
        self._fit_wrapped_labels(frame)
        self.status = tk.StringVar(value="Ready. Opening this window does not contact a website or AI model.")
        ttk.Label(footer, textvariable=self.status, wraplength=1000, style="Subtitle.TLabel").pack(anchor="w", pady=(10, 4))
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.pack(fill="x")
        self.refresh()
        self._poll_timer = self.window.after(100, self._poll)
        self.window.bind("<Destroy>", self._window_destroyed, add="+")
        self.window.protocol("WM_DELETE_WINDOW", self._close)

    def _tab(self, title):
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text=title)
        return tab

    def _button(self, parent, text, command, *, primary=False, **pack):
        button = ttk.Button(parent, text=text, command=command,
                            style="Primary.TButton" if primary else "TButton")
        button.pack(**({"side": "left", "padx": (0, 8)} | pack))
        self.action_buttons.append(button)
        return button

    def _field(self, parent, label, row, values=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        value = tk.StringVar()
        widget = (ttk.Combobox(parent, textvariable=value, values=values, state="readonly")
                  if values is not None else ttk.Entry(parent, textvariable=value))
        widget.grid(row=row, column=1, sticky="ew", pady=3)
        parent.columnconfigure(1, weight=1)
        return value

    def _fit_wrapped_labels(self, parent):
        """Keep help text readable when the window is made narrower."""
        for child in parent.winfo_children():
            if isinstance(child, ttk.Label) and child.cget("wraplength"):
                def fit(event, label=child):
                    available = event.width - 20
                    if label.winfo_manager() == "pack" and label.pack_info().get("side") in ("left", "right"):
                        available -= sum(sibling.winfo_reqwidth() + 16 for sibling in label.master.winfo_children()
                                         if sibling is not label)
                    label.configure(wraplength=max(180, available))
                parent.bind("<Configure>", fit, add="+")
            self._fit_wrapped_labels(child)

    def _scrolling_frame(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0, background="#ffffff")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        body = ttk.Frame(canvas)
        item = canvas.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
        return body

    def _build_jobs(self):
        metrics = ttk.Frame(self.jobs_tab)
        metrics.pack(fill="x", pady=(0, 12))
        self.metrics = {}
        for index, (key, title) in enumerate((("saved", "SAVED JOBS"), ("active", "ACTIVE APPLICATIONS"), ("due", "FOLLOW-UPS DUE"))):
            metrics.columnconfigure(index, weight=1, uniform="metric")
            card = ttk.Frame(metrics, padding=(14, 8), style="Card.TFrame")
            card.grid(row=0, column=index, sticky="ew", padx=(0, 10 if index < 2 else 0))
            self.metrics[key] = tk.StringVar(value="0")
            ttk.Label(card, textvariable=self.metrics[key], style="Metric.TLabel").pack(anchor="w")
            ttk.Label(card, text=title, style="Muted.TLabel", font=("Segoe UI", 9)).pack(anchor="w")
        self.job_summary = tk.StringVar(value="No saved jobs yet. Add a vacancy to get started.")
        ttk.Label(self.jobs_tab, textvariable=self.job_summary, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        toolbar = ttk.Frame(self.jobs_tab)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="Find a saved job").pack(side="left", padx=(0, 8))
        self.search = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.search, width=24).pack(side="left", padx=(0, 10))
        self.search.trace_add("write", lambda *_: self._fill_jobs())
        self.status_filter = tk.StringVar(value="All statuses")
        ttk.Combobox(toolbar, textvariable=self.status_filter, values=("All statuses", *STATUSES), state="readonly", width=14).pack(side="left", padx=(0, 10))
        self.status_filter.trace_add("write", lambda *_: self._fill_jobs())
        self._button(toolbar, "Refresh", self.refresh)
        self._button(toolbar, "Dashboard", self.show_dashboard)
        self._button(toolbar, "Add a job", lambda: self.tabs.select(self.import_tab))
        panes = ttk.Panedwindow(self.jobs_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left, right_container = ttk.Frame(panes), ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(left, weight=3)
        panes.add(right_container, weight=2)
        right = self._scrolling_frame(right_container)
        self.job_tree = ttk.Treeview(left, columns=("company", "title", "status"), show="headings", selectmode="browse")
        for name, label, width in (("company", "Company", 125), ("title", "Position", 245), ("status", "Status", 90)):
            self.job_tree.heading(name, text=label)
            self.job_tree.column(name, width=width, minwidth=60)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.job_tree.yview)
        self.job_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.job_tree.pack(fill="both", expand=True)
        self.job_tree.tag_configure("alternate", background="#f5f8fa")
        self.job_tree.bind("<<TreeviewSelect>>", self._job_selected)
        self.job_heading = tk.StringVar(value="Select a job to view its details")
        ttk.Label(right, textvariable=self.job_heading, style="Heading.TLabel", wraplength=360).pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(right)
        form.pack(fill="x")
        names = [("status", "Status"), ("application_date", "Applied (YYYY-MM-DD)"),
                 ("follow_up_date", "Follow-up (YYYY-MM-DD)"), ("next_action", "Next action"),
                 ("contact", "Contact"), ("cv_version", "CV used"), ("cover_letter", "Letter used"),
                 ("interview_stage", "Interview stage"), ("interview_dates", "Interview dates (comma separated)")]
        self.tracking = {}
        for row, (name, label) in enumerate(names):
            self.tracking[name] = self._field(form, label, row, STATUSES if name == "status" else None)
        self.status_choice = form.grid_slaves(row=0, column=1)[0]
        self.status_choice.bind("<<ComboboxSelected>>", self.save_status)
        self.tracking_notice = tk.StringVar(value="Changes save automatically after typing. Save progress saves immediately.")
        ttk.Label(right, textvariable=self.tracking_notice, wraplength=350,
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(right, text="Notes").pack(anchor="w", pady=(6, 2))
        self.notes = ScrolledText(right, height=4, wrap="word", font=("Segoe UI", 10))
        self.notes.pack(fill="both", expand=True)
        for name, variable in self.tracking.items():
            if name != 'status':
                variable.trace_add('write', self._tracking_edited)
        self.notes.bind('<<Modified>>', self._notes_edited)
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(10, 0))
        self._button(buttons, "Save progress", self.save_tracking, primary=True)
        self._button(buttons, "History", self.show_history)
        self._button(buttons, "Create documents", self.documents_for_selected)
        detail_buttons = ttk.Frame(right)
        detail_buttons.pack(fill="x", pady=(8, 0))
        self._button(detail_buttons, "View vacancy details", self.show_vacancy)
        self._button(detail_buttons, "Supporting sources", self.open_job_context)
        match_actions = ttk.Frame(right)
        match_actions.pack(fill="x", pady=(8, 0))
        self._button(match_actions, "Assess match…", self.open_job_match)

    def _build_import(self):
        ttk.Label(self.import_tab, text="Add a vacancy from a link or pasted text", style="Heading.TLabel").pack(anchor="w")
        top = ttk.Frame(self.import_tab)
        top.pack(fill="x", pady=(8, 5))
        self.import_url = tk.StringVar()
        link = ttk.Entry(top, textvariable=self.import_url)
        link.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.import_fields.append(link)
        self._import_button(top, "Fetch details", self.fetch_job)
        self._import_button(top, "Paste job text…", self.paste_job_text)
        self._import_button(top, "Open YAML file", self.load_yaml_file)
        ttk.Label(self.import_tab, text="Fetching, local extraction and saving use no AI. Imports can run while documents are generating.",
                  wraplength=1000).pack(anchor="w", pady=(0, 8))
        ai_actions = ttk.Frame(self.import_tab)
        ai_actions.pack(fill="x", pady=(0, 6))
        self._import_button(ai_actions, "Extract missing details with AI", self.extract_import_with_ai)
        self._import_button(ai_actions, "View extraction evidence", self.show_import_evidence)
        self.import_ai_notice = tk.StringVar(value="Optional AI extraction sends vacancy text and filled fields to your selected model; provider usage applies. Review before saving.")
        ttk.Label(self.import_tab, textvariable=self.import_ai_notice, wraplength=1000,
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.import_task_status = tk.StringVar(value="Ready to import. No model or sign-in is needed.")
        ttk.Label(self.import_tab, textvariable=self.import_task_status, style="Muted.TLabel", wraplength=1000).pack(anchor="w", pady=(0, 5))
        self.import_note = tk.StringVar(value="You can also fill the fields below yourself.")
        bottom = ttk.Frame(self.import_tab)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self._import_button(bottom, "Save reviewed job", self.save_job, primary=True)
        self._import_button(bottom, "Open imported files", lambda: self.open_path(self.service.root / "data" / "imported_jobs"))
        self._import_button(bottom, "Clear form", self.clear_import)
        container = ttk.Frame(self.import_tab)
        container.pack(fill="both", expand=True)
        content = self._scrolling_frame(container)
        content.master.configure(height=180)
        ttk.Label(content, textvariable=self.import_note, wraplength=1000).pack(anchor="w", pady=(0, 8))
        form = ttk.Frame(content)
        form.pack(fill="x")
        self.job_fields = {}
        for row, (name, label) in enumerate((('company', 'Company'), ('title', 'Position'),
                                            ('location', 'Location'), ('source_url', 'Vacancy URL (optional)'),
                                            ('source', 'Found through'), ('role_family', 'Role type (optional)'))):
            self.job_fields[name] = self._field(form, label, row)
            self.import_fields.append(form.grid_slaves(row=row, column=1)[0])
        ttk.Label(content, text="Vacancy description").pack(anchor="w", pady=(8, 3))
        self.description = ScrolledText(content, height=8, wrap="word", font=("Segoe UI", 10))
        self.description.pack(fill="x")
        self.import_fields.append(self.description)

    def _build_references(self):
        ttk.Label(self.refs_tab, text="Examples that sound like you", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.refs_tab, text="Store CVs and cover letters you like. Select examples for each application in Create documents.", wraplength=1000, style="Muted.TLabel").pack(anchor="w", pady=(5, 10))
        self.reference_summary = tk.StringVar(value="No examples yet. Add a CV or cover letter to build your library.")
        ttk.Label(self.refs_tab, textvariable=self.reference_summary, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        types = ttk.Frame(self.refs_tab)
        types.pack(fill="x", pady=(0, 12))
        ttk.Label(types, text="Document type").pack(side="left", padx=(0, 8))
        self.reference_type = tk.StringVar(value="CV")
        ttk.Combobox(types, textvariable=self.reference_type, state="readonly", values=("CV", "Cover letter"), width=15).pack(side="left", padx=(0, 12))
        self._button(types, "Add reference files", self.add_references, primary=True)
        self._button(types, "Set selected reference type", self.set_reference_type)
        listing = ttk.Frame(self.refs_tab)
        listing.pack(fill="both", expand=True)
        self.ref_tree = ttk.Treeview(listing, columns=("kind", "label", "file", "format"), show="headings", selectmode="browse")
        for name, label, width in (("kind", "Type", 110), ("label", "Label", 260), ("file", "Original file", 360), ("format", "Format", 75)):
            self.ref_tree.heading(name, text=label)
            self.ref_tree.column(name, width=width, minwidth=60)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.ref_tree.yview)
        self.ref_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.ref_tree.pack(fill="both", expand=True)
        bottom = ttk.Frame(self.refs_tab)
        bottom.pack(side="bottom", fill="x", pady=(10, 0), before=listing)
        self._button(bottom, "Remove library copy", self.remove_reference)
        self._button(bottom, "Open library folder", lambda: self.open_path(self.service.reference_library))
        ttk.Label(self.refs_tab, text="Adding files stays local. Examples guide tone and structure; facts come from your verified profile. PDF, Word, text and LaTeX files are supported. Your originals stay unchanged.", wraplength=1000, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=(10, 0), before=bottom)

    def _build_documents(self):
        footer = ttk.Frame(self.docs_tab)
        footer.pack(side="bottom", fill="x")
        content = ttk.Frame(self.docs_tab)
        content.pack(fill="both", expand=True)
        body = self._scrolling_frame(content)
        self._document_canvas = body.master
        ttk.Label(body, text="One application. Two coordinated first drafts.", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Create the CV and cover letter together, then review and improve either document separately.", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        connection = ttk.Frame(body)
        connection.pack(fill="x", pady=(8, 0))
        self._button(connection, "Check setup", self.check_codex_connection)
        self.connection_status = tk.StringVar(value="Codex has not been checked yet. Reviewing information does not need a connection.")
        ttk.Label(connection, textvariable=self.connection_status, wraplength=740).pack(side="left", fill="x", expand=True)
        self.ai_summary = tk.StringVar()
        ttk.Label(body, textvariable=self.ai_summary, style='Muted.TLabel', wraplength=1000).pack(anchor='w', pady=(8, 0))
        ttk.Label(body, text="Choose one saved job").pack(anchor="w", pady=(12, 4))
        self.document_job = ttk.Combobox(body, state="readonly")
        self.document_job.pack(fill="x")
        source_actions = ttk.Frame(body)
        source_actions.pack(fill='x', pady=(6, 0))
        self._button(source_actions, 'Supporting sources for this job', self.document_supporting_sources)
        ttk.Label(source_actions, text='Papers, company articles and your notes', style='Muted.TLabel').pack(side='left')
        ttk.Label(body, text="Reference examples: choose a document on the left, then add it to your selection.").pack(anchor="w", pady=(12, 4))
        picker = ttk.Frame(body)
        picker.pack(fill="both", expand=True)
        picker.columnconfigure(0, weight=1, uniform="reference")
        picker.columnconfigure(2, weight=1, uniform="reference")
        picker.rowconfigure(1, weight=1)
        ttk.Label(picker, text="Available in your library").grid(row=0, column=0, sticky="w")
        self.reference_selection_count = tk.StringVar(value="Selected for this application (0/3)")
        ttk.Label(picker, textvariable=self.reference_selection_count).grid(row=0, column=2, sticky="w")
        options = dict(selectmode="browse", exportselection=False, height=5, font=("Segoe UI", 10),
                       selectbackground="#d8e9f1", selectforeground="#173452", background="#f8fafc",
                       foreground="#23364a", relief="flat", highlightthickness=1, highlightbackground="#d4dfe8")
        lists = []
        for column in (0, 2):
            box = ttk.Frame(picker)
            box.grid(row=1, column=column, sticky="nsew", pady=4)
            listing = tk.Listbox(box, **options)
            scroll = ttk.Scrollbar(box, orient="vertical", command=listing.yview)
            listing.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            listing.pack(fill="both", expand=True)
            lists.append(listing)
        self.doc_refs, self.doc_selected_refs = lists
        actions = ttk.Frame(picker, padding=(8, 0))
        actions.grid(row=1, column=1)
        self._button(actions, "Add →", self.add_document_reference, side="top", pady=4)
        self._button(actions, "← Remove", self.remove_document_reference, side="top", pady=4)
        self.doc_refs.bind("<Double-Button-1>", lambda event: self.add_document_reference())
        self.doc_refs.bind("<Return>", lambda event: self.add_document_reference())
        self.doc_selected_refs.bind("<Double-Button-1>", lambda event: self.remove_document_reference())
        self.doc_selected_refs.bind("<Delete>", lambda event: self.remove_document_reference())
        self.reference_selection_note = tk.StringVar(value="Only documents in the selected list are sent. CV and letter examples share the three-document limit.")
        ttk.Label(footer, textvariable=self.reference_selection_note, wraplength=1000, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.ai_disclosure = tk.StringVar()
        ttk.Label(footer, textvariable=self.ai_disclosure, wraplength=1000, style="Muted.TLabel").pack(anchor="w", pady=(6, 6))
        self.compile_pdf = tk.BooleanVar(value=False)
        ttk.Checkbutton(footer, text="Also compile PDFs (requires a local LaTeX installation)", variable=self.compile_pdf).pack(anchor="w")
        buttons = ttk.Frame(footer)
        buttons.pack(fill="x", pady=(6, 4))
        self._button(buttons, "Review information (local)", lambda: self.create_documents(preview=True))
        self._button(buttons, "Generate CV + letter", lambda: self.create_documents(preview=False), primary=True)
        self.abort_generation_button = ttk.Button(buttons, text="Abort generation", command=self.abort_generation, state="disabled")
        self.abort_generation_button.pack(side="left", padx=(0, 8))
        self._button(buttons, "Open output folder", self.open_output)
        self._button(buttons, "My profile", lambda: self.tabs.select(self.profile_tab))
        self.document_result = tk.StringVar(value="Review information opens the source material, not a finished CV. Generate drafts creates the CV and letter in a new folder.")
        ttk.Label(footer, textvariable=self.document_result, wraplength=1000).pack(anchor="w", pady=(6, 0))

    def _build_review(self):
        ttk.Label(self.review_tab, text="Keep what works. Improve what doesn't.", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.review_tab, text="Choose an application. Edits update the same files; earlier versions are kept in history.", wraplength=1000, style="Muted.TLabel").pack(anchor="w", pady=(4, 10))
        versions = ttk.Frame(self.review_tab)
        versions.pack(fill="x")
        self.draft_choice = ttk.Combobox(versions, state="readonly")
        self.draft_choice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.draft_choice.bind("<<ComboboxSelected>>", lambda _: self.show_draft())
        self._button(versions, "Refresh versions", self.refresh_drafts)
        self.show_draft_history = tk.BooleanVar(value=False)
        ttk.Checkbutton(versions, text="Show history", variable=self.show_draft_history,
                        command=self.refresh_drafts).pack(side="left", padx=(0, 8))
        self._button(versions, "Edit text / notes", self.open_manual_editor, primary=True)
        scope = ttk.Frame(self.review_tab)
        scope.pack(fill="x", pady=(10, 6))
        self.review_document = tk.StringVar(value="cover_letter")
        ttk.Radiobutton(scope, text="Cover letter", variable=self.review_document, value="cover_letter", command=self.show_draft).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(scope, text="CV", variable=self.review_document, value="cv", command=self.show_draft).pack(side="left", padx=(0, 20))
        ttk.Label(scope, text="Text view · open the document to check its layout", style="Muted.TLabel").pack(side="left")
        self._button(scope, "Check exported CV…", self.check_exported_cv)
        self.draft_viewer = ScrolledText(self.review_tab, height=9, wrap="word", font=("Segoe UI", 11), padx=16, pady=12, spacing3=5)
        self.draft_viewer.pack(fill="both", expand=True)
        self.draft_viewer.configure(state="disabled")
        editing = ttk.Frame(self.review_tab)
        editing.pack(side="bottom", fill="x", before=self.draft_viewer)
        ttk.Label(editing, text="What would you like to change?", style="Heading.TLabel").pack(anchor="w", pady=(10, 3))
        ttk.Label(editing, text='For example: "Keep my opening, shorten the research paragraph, and make the ending more direct."', style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(0, 4))
        self.revision_feedback = ScrolledText(editing, height=3, wrap="word", font=("Segoe UI", 10), padx=10, pady=6)
        self.revision_feedback.pack(fill="x")
        self.revision_formatting_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(editing, text="Formatting only: show or hide the cover-letter recipient block; keep wording",
                        variable=self.revision_formatting_only).pack(anchor="w", pady=(6, 0))
        self.revision_compile_pdf = tk.BooleanVar(value=False)
        ttk.Checkbutton(editing, text="Also compile the revised PDF (requires local LaTeX)",
                        variable=self.revision_compile_pdf).pack(anchor="w", pady=(6, 0))
        actions = ttk.Frame(editing)
        actions.pack(fill="x", pady=(10, 6))
        self._button(actions, "Improve selected document", self.improve_document, primary=True)
        self._button(actions, "Compare previous version", self.compare_revision)
        self._button(actions, "Open version folder", self.open_draft_folder)
        self._button(actions, "Export to Word…", self.open_word_export)
        self.revision_result = tk.StringVar(value="Reading drafts stays local. Improve sends your feedback and the saved application context to Codex.")
        ttk.Label(editing, textvariable=self.revision_result, wraplength=1000, style="Muted.TLabel").pack(anchor="w")
        self.ai_revision_disclosure = tk.StringVar()
        ttk.Label(editing, textvariable=self.ai_revision_disclosure, wraplength=1000, style='Muted.TLabel').pack(anchor='w', pady=(5, 0))

    def check_exported_cv(self):
        try:
            draft = self._chosen_draft()
        except ValueError as exc:
            return self._error(exc)
        selected = filedialog.askopenfilename(
            parent=self.window, title="Choose the exported CV to check",
            initialdir=draft["directory"], filetypes=[("CV documents", "*.pdf *.docx")],
        )
        if selected:
            path = Path(selected)
            self._run("Reading the exported CV locally…",
                      lambda: self.service.check_exported_cv(draft["directory"], path),
                      lambda report: self._show_text("CV readability · " + path.name,
                          f"File: {path.name}\nSaved application: {draft['label']}\n\n{report}"))

    def _chosen_draft(self):
        index = self.draft_choice.current()
        if index < 0 or index >= len(self.saved_drafts):
            raise ValueError("Generate a CV and cover letter first, then choose a saved version here.")
        return self.saved_drafts[index]

    def refresh_drafts(self, selected=None):
        try:
            if selected is None and self.draft_choice.current() >= 0:
                selected = self._chosen_draft()['directory']
            self.saved_drafts = (self.service.list_drafts(include_history=True)
                                 if self.show_draft_history.get() else self.service.list_drafts())
            self.draft_choice.configure(values=[item['label'] for item in self.saved_drafts])
            if self.saved_drafts:
                index = next((i for i, item in enumerate(self.saved_drafts)
                              if selected is not None and Path(item['directory']) == Path(selected)), 0)
                self.draft_choice.current(index)
            else:
                self.draft_choice.set("")
            self.show_draft()
        except Exception as exc:
            self._error(exc)

    def show_draft(self):
        try:
            text = (self.service.draft_text(self._chosen_draft()['directory'], self.review_document.get())
                    if self.saved_drafts else "Your generated CVs and cover letters will appear here.\n\nStart in Create documents to prepare your first pair.")
        except Exception as exc:
            text = str(exc)
        self.draft_viewer.configure(state="normal")
        self.draft_viewer.delete("1.0", "end")
        self.draft_viewer.insert("1.0", text)
        self.draft_viewer.configure(state="disabled")

    def improve_document(self):
        try:
            source = Path(self._chosen_draft()['directory'])
            document = self.review_document.get()
            compile_pdf = self.revision_compile_pdf.get()
            formatting_only = self.revision_formatting_only.get()
            if formatting_only and document != "cover_letter":
                raise ValueError("Formatting only currently supports the cover-letter recipient block. Select Cover letter or turn it off for wording changes.")
            feedback = self.revision_feedback.get("1.0", "end-1c").strip()
            settings = self._selected_ai()
            if not feedback:
                raise ValueError("Write the changes you would like before choosing Improve.")
        except Exception as exc:
            return self._error(exc)
        def action():
            self._require_provider(settings)
            options = {"formatting_only": True} if formatting_only else {}
            return self.service.improve(source, document, feedback, compile_pdf=compile_pdf, provider_settings=settings, **options)
        def done(result):
            self.last_output = Path(result['output_root'])
            errors = result.get('validation_errors', []) + result.get('compile_errors', [])
            if errors:
                self.revision_result.set("Revision needs review: " + "\n".join(map(str, errors)))
            elif result.get("unchanged"):
                self.revision_result.set("No changes were applied. Your files and feedback are retained. See the explanation below.")
            else:
                self.review_document.set(document)
                self.refresh_drafts(selected=result['outputs'][0])
                if self.revision_feedback.get("1.0", "end-1c").strip() == feedback:
                    self.revision_feedback.delete("1.0", "end")
                self.revision_result.set("Application updated in the same folder. The previous version is saved in history. Use Compare previous version to review the changes.")
            if result.get('review_notes'):
                self.revision_result.set(self.revision_result.get() + "\n" + "\n".join(result['review_notes']))
            self.status.set("Revision finished. Review the result before use.")
        self.revision_result.set("Applying your feedback to the selected document…")
        self._run("Checking AI setup and revising your document; this can take several minutes…", action, done,
                  lambda exc: self.revision_result.set("The revision did not finish. " + str(exc)))

    def compare_revision(self):
        try:
            text = self.service.compare_draft(self._chosen_draft()['directory'], self.review_document.get())
            self._show_text("Changes from the previous version", text)
        except Exception as exc:
            self._error(exc)

    def open_draft_folder(self):
        try:
            self.open_path(self._chosen_draft()['directory'])
        except Exception as exc:
            self._error(exc)

    def refresh(self):
        if not self._flush_tracking():
            return
        try:
            previous_index = self.document_job.current()
            previous_job_id = self.jobs[previous_index]['job_id'] if 0 <= previous_index < len(self.jobs) else None
            self.jobs = self.service.jobs()
            self.references = self.service.refs()
            self._update_metrics()
            self._fill_jobs()
            previous = self.document_job.get()
            labels = [f"{job['company']} | {job['title']} | {job.get('location', '')}" for job in self.jobs]
            counts = {label: labels.count(label) for label in set(labels)}
            seen = {}
            for index, label in enumerate(labels):
                seen[label] = seen.get(label, 0) + 1
                if counts[label] > 1:
                    labels[index] = f"{label} ({seen[label]} of {counts[label]} postings)"
            self.document_job.configure(values=labels)
            selected_index = next((i for i, job in enumerate(self.jobs) if job['job_id'] == previous_job_id), None)
            if selected_index is not None:
                self.document_job.current(selected_index)
            elif previous in labels:
                self.document_job.current(labels.index(previous))
            elif labels:
                self.document_job.current(0)
            else:
                self.document_job.set("")
            self.ref_tree.delete(*self.ref_tree.get_children())
            for item in self.references:
                kind = "Cover letter" if item.get('document_type') == 'cover_letter' else "CV"
                self.ref_tree.insert("", "end", iid=item['id'], values=(kind, item['label'], item['source_name'], item['format'].upper()))
            self._refresh_document_references()
            self._refresh_profile_references()
            self.reference_summary.set(f"{len(self.references)} reference documents saved locally." if self.references
                                       else "No examples yet. Add a CV or cover letter to build your library.")
            self.refresh_drafts()
        except Exception as exc:
            self._error(exc)

    def _update_metrics(self):
        active = sum(job.get('status') in {'preparing', 'applied', 'interview'} for job in self.jobs)
        self.metrics['saved'].set(str(len(self.jobs)))
        self.metrics['active'].set(str(active))
        today = local_today().isoformat()
        due = sum(bool(job.get('follow_up_date')) and str(job['follow_up_date']) <= today
                  and job.get('status') not in {'rejected', 'withdrawn', 'offer'} for job in self.jobs)
        self.metrics['due'].set(str(due))

    def _fill_jobs(self):
        if self._filter_loading:
            return
        if not self._flush_tracking():
            # Keep the current row and its unfinished form visible.
            self._filter_loading = True
            try:
                self.search.set(self._last_job_filters[0])
                self.status_filter.set(self._last_job_filters[1])
            finally:
                self._filter_loading = False
            return
        selected = self.job_tree.selection()
        query = self.search.get().strip().casefold()
        self._last_job_filters = (self.search.get(), self.status_filter.get())
        self.job_tree.delete(*self.job_tree.get_children())
        for job in self.jobs:
            if self.status_filter.get() != "All statuses" and job.get('status') != self.status_filter.get():
                continue
            if query and query not in " ".join(str(job.get(key, '')) for key in ('company', 'title', 'location', 'status')).casefold():
                continue
            tag = 'alternate' if len(self.job_tree.get_children()) % 2 else ''
            self.job_tree.insert("", "end", iid=job['job_id'], values=(job['company'], job['title'], job['status']), tags=(tag,))
        count = len(self.job_tree.get_children())
        self.job_summary.set(f"Showing {count} of {len(self.jobs)} saved jobs. Select one to view its progress." if count
                             else "No jobs match these filters." if self.jobs
                             else "No saved jobs yet. Add a vacancy to get started.")
        if selected and self.job_tree.exists(selected[0]):
            self.job_tree.selection_set(selected[0])
        elif self.selected_job:
            self._job_selected()

    def _job_selected(self, _event=None):
        selected = self.job_tree.selection()
        current = next((job for job in self.jobs if selected and job['job_id'] == selected[0]), None)
        # Tk may emit several selection events for the same row.
        if self.selected_job is current:
            return
        if not self._flush_tracking():
            if self.selected_job and self.job_tree.exists(self.selected_job['job_id']):
                self.job_tree.selection_set(self.selected_job['job_id'])
            return
        self._tracking_loading = True
        try:
            self._load_tracking_form(current)
        finally:
            self._tracking_loading = False

    def _load_tracking_form(self, current):
        self._cancel_tracking_save()
        self.selected_job = current
        if current is None:
            self.job_heading.set("Select a job to view its details")
            for variable in self.tracking.values():
                variable.set('')
            self.notes.delete('1.0', 'end')
            self.notes.edit_modified(False)
            self.tracking_notice.set("Select a job to edit its progress.")
            return
        self.job_heading.set(f"{self.selected_job['company']} — {self.selected_job['title']}")
        for name, value in self.tracking.items():
            current = self.selected_job.get(name)
            if name == 'status' and current == 'untracked':
                current = 'interested'
            if name == 'interview_dates':
                current = ", ".join(current or [])
            value.set(current or '')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', self.selected_job.get('notes') or '')
        self.notes.edit_modified(False)
        self.tracking_notice.set("Changes save automatically after typing. Save progress saves immediately.")

    def _cancel_tracking_save(self):
        if self._tracking_timer is not None:
            self.window.after_cancel(self._tracking_timer)
            self._tracking_timer = None

    def _notes_edited(self, _event=None):
        if self.notes.edit_modified():
            self.notes.edit_modified(False)
            self._tracking_edited()

    def _tracking_edited(self, *_):
        if self._tracking_loading or not self.selected_job:
            return
        self._cancel_tracking_save()
        if self._tracking_changes():
            self.tracking_notice.set("Changes not saved yet. Saving after typing…")
            self._tracking_timer = self.window.after(600, self._flush_tracking)
        else:
            self.tracking_notice.set("All progress changes saved locally.")

    def _tracking_changes(self):
        if not self.selected_job:
            return {}
        job = self.selected_job
        changes = {}
        for name, value in self.tracking.items():
            current = value.get().strip()
            if name == 'interview_dates':
                current = [day.strip() for day in current.split(',') if day.strip()]
                old = job.get(name) or []
            else:
                current = current or None
                old = job.get(name) or None
                if name == 'status' and old == 'untracked':
                    old = 'interested'
            if current != old:
                changes[name] = current
        notes = self.notes.get('1.0', 'end-1c').strip() or None
        if notes != (job.get('notes') or None):
            changes['notes'] = notes
        if changes and job.get('status') == 'untracked':
            changes.setdefault('status', self.tracking['status'].get() or 'interested')
        return changes

    def _flush_tracking(self, *, report_error=False):
        """Commit current edits before any action can replace their form.

        This short local write stays on the UI thread. A delayed worker callback
        must not reload an older snapshot over text entered after clicking Save.
        """
        self._cancel_tracking_save()
        changes = self._tracking_changes()
        if not changes:
            if self.selected_job:
                self.tracking_notice.set("All progress changes saved locally.")
            return True
        job = self.selected_job
        try:
            saved = self.service.update_tracking(job['job_id'], changes)
        except Exception as exc:
            self.tracking_notice.set(f"Changes not saved: {exc}. Your edits are still here; correct them or retry Save progress.")
            if report_error:
                self.tabs.select(self.jobs_tab)
                self._error(exc)
            return False
        job.update(saved)
        self._tracking_loading = True
        try:
            if not self.tracking['application_date'].get().strip() and saved.get('application_date'):
                self.tracking['application_date'].set(saved['application_date'])
        finally:
            self._tracking_loading = False
        if self.job_tree.exists(job['job_id']):
            self.job_tree.item(job['job_id'], values=(job['company'], job['title'], job['status']))
        self._update_metrics()
        self.tracking_notice.set("All progress changes saved locally.")
        if not self.busy:
            self.status.set("Progress saved.")
        return True

    def save_status(self, _event=None):
        """A status selection is one small local save, reflected immediately."""
        if not self.selected_job:
            return
        job = self.selected_job
        previous = job.get('status', 'interested')
        requested = self.tracking['status'].get()
        if requested == previous:
            return
        if self.busy:
            self.tracking['status'].set(previous if previous != 'untracked' else 'interested')
            return self._error(ValueError("Wait for the current action to finish, then change the status."))
        date_was_edited = 'application_date' in self._tracking_changes()
        try:
            saved = self.service.update_tracking(job['job_id'], {'status': requested})
        except Exception as exc:
            self.tracking['status'].set(previous if previous != 'untracked' else 'interested')
            self.tracking_notice.set("Status was not saved. Please try again.")
            return self._error(exc)
        job.update(saved)
        if not date_was_edited and not self.tracking['application_date'].get().strip() and saved.get('application_date'):
            self.tracking['application_date'].set(saved['application_date'])
        self.job_tree.item(job['job_id'], values=(job['company'], job['title'], job['status']))
        # Keep the updated record visible when it has left the active status filter.
        if self.status_filter.get() not in {'All statuses', requested}:
            self.status_filter.set('All statuses')
        self._update_metrics()
        if self._tracking_changes():
            self._tracking_edited()
            self.tracking_notice.set(f"Status saved: {requested}. Other edits save automatically after typing.")
        else:
            self.tracking_notice.set(f"Status saved: {requested}.")
        self.status.set(f"Status saved: {requested}.")

    def save_tracking(self):
        if not self.selected_job:
            return self._error(ValueError("Select a saved job first."))
        self._flush_tracking(report_error=True)

    def documents_for_selected(self):
        if not self.selected_job:
            return self._error(ValueError("Select a saved job first."))
        index = next(i for i, job in enumerate(self.jobs) if job['job_id'] == self.selected_job['job_id'])
        self.document_job.current(index)
        self.tabs.select(self.docs_tab)

    def document_supporting_sources(self):
        index = self.document_job.current()
        if index < 0 or index >= len(self.jobs):
            return self._error(ValueError('Choose a saved job first.'))
        self.open_job_context(self.jobs[index])

    def fetch_job(self):
        if self._import_worker is not None:
            return
        url = self.import_url.get().strip()
        if not url:
            return self._error(ValueError("Paste a vacancy link first."))
        self._run_import("Fetching vacancy details from the website…", lambda: self.service.import_url(url), self._fetched)

    def _fetched(self, result):
        self._fill_draft(result['job'])
        self.import_ai_evidence = dict(result.get('ai_evidence') or {})
        self.import_unresolved_fields = dict(result.get('unresolved_fields') or {})
        self.draft_path = result.get('yaml_path')
        warnings = list(result.get('warnings') or [])
        if result.get('missing_fields'):
            warnings.insert(0, "Fill in: " + ", ".join(result['missing_fields']))
        self.import_note.set(" | ".join(warnings) or "Details extracted. Check them below, then save the reviewed job.")
        self.status.set("Vacancy draft saved locally. Review the fields, then Save reviewed job.")

    def _fill_draft(self, raw):
        if self._import_worker is not None:
            raise ValueError("Wait for the current vacancy import to finish before replacing its form.")
        self.discovery_draft = None
        self.import_ai_evidence = {}
        self.import_unresolved_fields = {}
        self.draft = dict(raw)
        for name, variable in self.job_fields.items():
            variable.set(raw.get(name) or '')
        self.description.delete('1.0', 'end')
        self.description.insert('1.0', raw.get('description') or raw.get('description_excerpt') or '')

    def clear_import(self):
        if self._import_worker is not None:
            return
        self._fill_draft({})
        self.import_url.set('')
        self.draft_path = None
        self.import_note.set("Paste a link or job text, open a YAML file, or fill the details yourself.")

    def load_yaml_file(self):
        if self._import_worker is not None:
            return
        filename = filedialog.askopenfilename(parent=self.window, title="Open a saved vacancy draft",
                                             initialdir=self.service.root / 'data', filetypes=[("Vacancy YAML", "*.yaml *.yml")])
        if filename:
            self._run_import("Opening saved draft…", lambda: self.service.load_draft(Path(filename)), self._loaded)

    def _loaded(self, raw):
        self._fill_draft(raw)
        self.import_note.set("Review these saved details, then save the job to the database.")

    def save_job(self):
        if self._import_worker is not None:
            return
        raw = dict(self.draft)
        for name, variable in self.job_fields.items():
            raw[name] = variable.get().strip()
        raw['source'] = raw['source'] or 'unknown'
        raw['role_family'] = raw['role_family'] or 'Unclassified'
        raw['description'] = self.description.get('1.0', 'end-1c').strip()
        candidate = dict(self.discovery_draft) if self.discovery_draft else None
        if candidate:
            candidate_id = str(candidate.get('id') or '')
            identity = ('job_ext_' + candidate_id.removeprefix('candidate_')) if candidate_id.startswith('candidate_') else None
        else:
            identity = stable_job_id(raw.get('company', ''), raw.get('title', ''), raw.get('location', ''))
        if identity and identity == self._generation_job_id:
            return self._error(ValueError("This vacancy is being used to generate documents. Wait for that run to finish before updating it. You can still import other jobs."))
        self._import_job_id = identity
        action = (lambda: self.service.save_discovered_job(candidate, raw)) if candidate else (lambda: self.service.save_job(raw))
        self._run_import("Saving the reviewed vacancy…", action, self._job_saved)

    def _job_saved(self, result):
        job_id, action = result
        self.search.set('')
        self.status_filter.set('All statuses')
        self.refresh()
        self.job_tree.selection_set(job_id)
        self.job_tree.see(job_id)
        self._job_selected()
        self.tabs.select(self.jobs_tab)
        self.status.set(f"Job saved ({action}). It is available in Jobs and Create documents.")

    def add_references(self):
        document_type = "cover_letter" if self.reference_type.get() == "Cover letter" else "cv"
        filenames = filedialog.askopenfilenames(parent=self.window, title="Choose reference documents",
                                               filetypes=[("Reference documents", "*.pdf *.docx *.txt *.md *.tex")])
        if not filenames:
            return
        def add_all():
            added, failed = [], []
            for filename in filenames:
                try:
                    added.append(self.service.add_ref(Path(filename), document_type=document_type))
                except Exception as exc:
                    failed.append(f"{Path(filename).name}: {exc}")
            return added, failed
        def done(result):
            added, failed = result
            self._saved(f"{len(added)} reference file(s) added or already present. No model was called.")
            if failed:
                self._error(ValueError('\n'.join(failed)))
        self._run("Reading reference files locally…", add_all, done)

    def set_reference_type(self):
        selected = self.ref_tree.selection()
        if not selected:
            return self._error(ValueError("Select a reference, then choose its document type above."))
        document_type = "cover_letter" if self.reference_type.get() == "Cover letter" else "cv"
        self._run("Updating the reference type…",
                  lambda: self.service.set_ref_type(selected[0], document_type),
                  lambda _: self._saved("Reference type updated. The original file is unchanged."))

    def remove_reference(self):
        selected = self.ref_tree.selection()
        if not selected:
            return self._error(ValueError("Select a reference to remove from the library."))
        reference_id = selected[0]
        self._run("Removing the library copy…", lambda: self.service.remove_ref(reference_id),
                  lambda _: self._saved("Library copy removed. The original file is unchanged."))

    def _refresh_document_references(self):
        by_id = {item['id']: item for item in self.references}
        self.chosen_reference_ids = [identifier for identifier in self.chosen_reference_ids if identifier in by_id]
        self.available_reference_ids = [item['id'] for item in self.references if item['id'] not in self.chosen_reference_ids]
        for listing, identifiers in ((self.doc_refs, self.available_reference_ids),
                                     (self.doc_selected_refs, self.chosen_reference_ids)):
            listing.delete(0, 'end')
            for identifier in identifiers:
                item = by_id[identifier]
                kind = 'Cover letter' if item.get('document_type') == 'cover_letter' else 'CV'
                listing.insert('end', f"[{kind}] {item['label']} ({item['source_name']})")
        self.reference_selection_count.set(f"Selected for this application ({len(self.chosen_reference_ids)}/3)")
        self.reference_selection_note.set("Only documents in the selected list are sent. Examples guide wording; the document templates set the PDF layout.")

    def add_document_reference(self):
        if self.busy:
            return
        selected = self.doc_refs.curselection()
        if not selected:
            self.reference_selection_note.set("Choose an available reference, then click Add →.")
            return
        if len(self.chosen_reference_ids) >= 3:
            self.reference_selection_note.set("Three references are already selected. Remove one before adding another.")
            return
        identifier = self.available_reference_ids[selected[0]]
        self.chosen_reference_ids.append(identifier)
        self._refresh_document_references()
        self.doc_selected_refs.selection_set('end')

    def remove_document_reference(self):
        if self.busy:
            return
        selected = self.doc_selected_refs.curselection()
        if not selected:
            self.reference_selection_note.set("Choose a selected reference, then click ← Remove.")
            return
        del self.chosen_reference_ids[selected[0]]
        self._refresh_document_references()

    def create_documents(self, *, preview):
        if self._worker is not None or self._closed:
            return
        if self._profile_has_unsaved_changes():
            self.tabs.select(self.profile_tab)
            return self._error(ValueError("Save your changes in My profile before preparing documents."))
        index = self.document_job.current()
        if index < 0:
            return self._error(ValueError("Save and choose a job first."))
        references = list(self.chosen_reference_ids)
        if len(references) > 3:
            return self._error(ValueError("Select up to three reference documents in total."))
        job_id = self.jobs[index]['job_id']
        if self._import_job_id == job_id:
            return self._error(ValueError("This vacancy is still being saved. Wait for its save to finish before creating documents for it."))
        compile_pdf = self.compile_pdf.get()
        try:
            settings = self._selected_ai()
        except ValueError as exc:
            return self._error(exc)
        if preview:
            action = lambda: self.service.preview(job_id, references, provider_settings=settings)
        else:
            token = CancellationToken()
            self._generation_cancel = token
            self._generation_job_id = job_id
            self._generation_tab = self.tabs.select()
            self._generation_compile_pdf = compile_pdf
            def action():
                with cancellation_scope(token):
                    try:
                        token.check()
                        connection = self._require_provider(settings)
                        token.check()
                        result = self.service.generate(job_id, references, compile_pdf=compile_pdf, provider_settings=settings)
                        token.check()
                        result['provider_status'] = connection
                        return result
                    except Exception as exc:
                        aborted = token.is_cancelled or isinstance(exc, GenerationCancelled)
                        mark_incomplete(token, aborted=aborted)
                        if aborted:
                            raise GenerationCancelled("Generation aborted.") from None
                        raise
        self.document_result.set("Preparing the information for your review…" if preview else "Checking AI setup and generating new drafts…")
        self._run("Preparing information for local review…" if preview else "Checking AI setup and generating drafts; this can take several minutes…",
                  action, lambda result: self._documents_ready(result, preview), self._documents_failed)

    def _update_generation_control(self):
        button = getattr(self, 'abort_generation_button', None)
        if button is None or not button.winfo_exists():
            return
        token = self._generation_cancel
        enabled = self._worker is not None and token is not None and not token.is_cancelled
        button.configure(state="normal" if enabled else "disabled")

    def abort_generation(self):
        token = self._generation_cancel
        if self._worker is None or token is None or token.is_cancelled:
            return False
        token.cancel()
        self._action_label = "Stopping generation…"
        self.status.set(self._action_label)
        self.document_result.set("Stopping generation… Waiting for the active request or process to close safely.")
        self._update_generation_control()
        return True

    @staticmethod
    def _connection_text(result):
        if 'ready' in result:
            return str(result.get('detail') or 'AI setup check finished.')
        if not result.get('installed'):
            return "Codex could not be found or started. " + str(result.get('detail') or 'Check its installation, then try again.')
        if not result.get('authenticated'):
            return str(result.get('detail') or "Codex was found, but sign-in is needed. Run codex login, then check the connection again.")
        return "Codex found and signed in. Ready to request drafts."

    def check_codex_connection(self):
        try:
            settings = self._selected_ai()
        except ValueError as exc:
            return self._error(exc)
        self._run("Checking the selected AI setup…", lambda: self._check_provider(settings),
                  self._connection_ready)

    def _connection_ready(self, result):
        self.connection_status.set(self._connection_text(result))
        self.status.set("Connection check finished. No document generation was requested.")

    def _documents_failed(self, error):
        self._generation_job_id = None
        self._generation_cancel = None
        self._update_generation_control()
        if isinstance(error, GenerationCancelled):
            self.document_result.set("Generation aborted. Existing documents are unchanged. Any partial output is marked as aborted.")
            self.status.set("Generation aborted. You can start a new run.")
        else:
            self.document_result.set("The new run did not finish.\n" + str(error))

    def _documents_ready(self, result, preview):
        token = self._generation_cancel
        if not preview and token is not None:
            if token.is_cancelled:
                mark_incomplete(token, aborted=True)
                self._documents_failed(GenerationCancelled("Generation aborted."))
                return
            finish_outputs(token)
        self._generation_cancel = None
        self._generation_job_id = None
        self._update_generation_control()
        self.last_output = Path(result['output_root'])
        errors = result.get('validation_errors', []) + result.get('compile_errors', [])
        if errors:
            text = "This run needs review:\n" + '\n'.join(map(str, errors))
        else:
            text = "Information ready for review. No model was called; no CV or letter has been generated." if preview else "Drafts ready. Review their facts and wording before use."
        self.document_result.set(text + f"\nSaved in: {self.last_output}")
        if result.get('codex_status'):
            self.connection_status.set(self._connection_text(result['codex_status']))
        if result.get('provider_status'):
            self.connection_status.set(self._connection_text(result['provider_status']))
        self.status.set("Information ready for review." if preview else "Generation finished. See the result above.")
        if preview and not errors:
            provider = PROVIDER_LABELS[self._selected_ai()['provider']]
            self._show_text("Information to be sent to " + provider, self.service.review_text(result))
        elif not preview and not result.get('validation_errors') and result.get('outputs'):
            self.revision_compile_pdf.set(getattr(self, '_generation_compile_pdf', self.compile_pdf.get()))
            self.refresh_drafts(selected=result['outputs'][0])
            if self._import_worker is None and self.tabs.select() == getattr(self, '_generation_tab', self.tabs.select()):
                self.tabs.select(self.review_tab)

    def open_output(self):
        if self.last_output is None:
            return self._error(ValueError("Review the information or generate drafts first."))
        self.open_path(self.last_output)

    def open_path(self, path):
        path = Path(path)
        if not path.exists():
            return self._error(ValueError("This folder or file has not been created yet."))
        try:
            resolved = str(path.resolve())
            if sys.platform == "win32":
                os.startfile(resolved)
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, resolved], shell=False)
        except (AttributeError, OSError) as exc:
            self._error(exc)

    def show_dashboard(self):
        if not self._flush_tracking(report_error=True):
            return
        try:
            self._show_text("Application dashboard", self.service.dashboard_text())
        except Exception as exc:
            self._error(exc)

    def show_history(self):
        import json
        if not self.selected_job:
            return self._error(ValueError("Select a saved job first."))
        if not self._flush_tracking(report_error=True):
            return
        try:
            events = self.service.history(self.selected_job['job_id'])
            self._show_text("Application history", json.dumps(events, ensure_ascii=False, indent=2) if events else "No history recorded yet.")
        except Exception as exc:
            self._error(exc)

    def show_vacancy(self):
        if not self.selected_job:
            return self._error(ValueError("Select a saved job first."))
        job = self.selected_job
        text = (f"{job['title']}\n{job['company']} — {job.get('location', '')}\n\n"
                f"{job.get('source_url', '')}\n\n{job.get('description', '')}")
        self._show_text("Saved vacancy details", text)

    def _show_text(self, title, text):
        window = tk.Toplevel(self.window)
        window.title(title)
        window.geometry('850x600')
        viewer = ScrolledText(window, wrap='word', font=('Segoe UI', 10), padx=12, pady=12)
        viewer.pack(fill='both', expand=True)
        viewer.insert('1.0', text)
        viewer.configure(state='disabled')

    def _saved(self, text):
        self.refresh()
        self.status.set(text)

    def _error(self, error):
        messagebox.showerror("Job Radar", str(error), parent=self.window)

    def _run(self, label, action, done, failed=None):
        if self._worker is not None or self._closed:
            return
        self.busy = True
        self._action_started = time.monotonic()
        self._action_label = label
        self._last_elapsed_second = -1
        self.status.set(label)
        self.progress.start(12)
        self.action_buttons = [button for button in self.action_buttons if button.winfo_exists()]
        # Keep UI-bound closures owned by the UI until the worker has exited.
        # Publishing its result happens before Thread.run releases its target.
        self._active_action = (action, done, failed)
        results = self.results
        def worker():
            try:
                results.put((action(), None))
            except Exception as exc:
                results.put((None, exc))
        self._worker = threading.Thread(target=worker, daemon=True, name="JobRadarAction")
        self._update_task_controls()
        self._worker.start()

    def _poll(self):
        if self._closed:
            return
        if self._poll_timer is not None:
            self.window.after_cancel(self._poll_timer)
            self._poll_timer = None
        if self._worker is not None and hasattr(self, '_action_started'):
            elapsed = int(time.monotonic() - self._action_started)
            if elapsed != self._last_elapsed_second:
                self._last_elapsed_second = elapsed
                self.status.set(f"{self._action_label} · elapsed {elapsed // 60}:{elapsed % 60:02d}")
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join()
            self._worker = None
            _, done, failed = self._active_action
            self._active_action = None
            result, error = self.results.get_nowait()
            self.busy = self._import_worker is not None
            if not self.busy:
                self.progress.stop()
            self._update_task_controls()
            if error is not None:
                self.status.set("The action could not finish. Your saved records remain available.")
                if failed is not None:
                    failed(error)
                if not isinstance(error, GenerationCancelled):
                    self._error(error)
            else:
                try:
                    done(result)
                except Exception as exc:
                    if failed is not None:
                        failed(exc)
                    self._error(exc)
        if not self._closed:
            self._poll_import()
            self._poll_timer = self.window.after(100, self._poll)

    def _window_destroyed(self, event):
        if event.widget is not self.window:
            return
        self._closed = True
        if self._poll_timer is not None:
            self.window.after_cancel(self._poll_timer)
            self._poll_timer = None
        self._cancel_tracking_save()

    def _close(self):
        if self.busy:
            messagebox.showinfo("Work in progress", "Wait for the current action to finish before closing Job Radar.", parent=self.window)
            return
        if not self._flush_tracking(report_error=True):
            return
        if not self._profile_can_leave():
            return
        self.window.destroy()


def main():
    window = tk.Tk()
    JobRadarApp(window)
    window.mainloop()


if __name__ == '__main__':
    main()
