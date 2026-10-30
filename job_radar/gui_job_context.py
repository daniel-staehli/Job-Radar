"""Local editor for papers, company articles and notes attached to one job."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import job_context


class JobContextView:
    def open_job_context(self, job=None):
        job = job or self.selected_job
        if not job:
            self._error(ValueError("Select a saved job first."))
            return None
        try:
            return JobContextDialog(self.window, self.service.root, job)
        except (ValueError, OSError) as exc:
            self._error(exc)
            return None


class JobContextDialog:
    def __init__(self, parent, root: Path, job: dict):
        self.root = root
        self.job_id = job["job_id"]
        self.records = job_context.list_sources(root, self.job_id)
        self.source_id = None
        self.origin = {"source_name": "", "source_sha256": ""}
        self.loading = False
        self.window = tk.Toplevel(parent)
        self.window.title("Supporting references — " + job.get("company", "Saved job"))
        self.window.geometry("940x710")
        self.window.minsize(820, 580)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{job.get('company', '')} — {job.get('title', 'Saved job')}",
                  style="Heading.TLabel", wraplength=880).pack(anchor="w")
        ttk.Label(frame, text="Add papers, company articles or your own notes for this vacancy. Links stay local until generation; saving a link does not read the website.",
                  wraplength=880, style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        bottom = ttk.Frame(frame)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.feedback = tk.StringVar(value="Select a reference to edit it, or start a new one.")
        ttk.Label(bottom, textvariable=self.feedback, wraplength=700).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Save reference", command=self.save, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="New reference", command=self.new).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Remove selected", command=self.remove).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Done", command=self.close).pack(side="right")
        body = ttk.Panedwindow(frame, orient="horizontal")
        body.pack(fill="both", expand=True)
        listing = ttk.Frame(body, width=250)
        editor = ttk.Frame(body, padding=(14, 0, 0, 0))
        body.add(listing, weight=1)
        body.add(editor, weight=3)
        ttk.Label(listing, text="References for this job").pack(anchor="w", pady=(0, 6))
        self.table = ttk.Treeview(listing, columns=("included",), show="tree headings", selectmode="browse", height=8)
        self.table.heading("#0", text="Title")
        self.table.column("#0", width=170, minwidth=120)
        self.table.heading("included", text="Use")
        self.table.column("included", width=45, minwidth=40, stretch=False)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.select)
        self.title = tk.StringVar()
        self.url = tk.StringVar()
        self.included = tk.BooleanVar(value=True)
        fields = ttk.Frame(editor)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        for row, (label, value) in enumerate((("Title", self.title), ("Link (optional)", self.url))):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
            ttk.Entry(fields, textvariable=value).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(editor, text="Include this reference when generating new documents", variable=self.included).pack(anchor="w", pady=(8, 4))
        ttk.Label(editor, text="Background only: these references do not add candidate facts. Generation uses up to 8 included references; long text is shortened in the request preview.",
                  wraplength=590, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(editor, text="Your notes / why this matters").pack(anchor="w")
        self.notes = ScrolledText(editor, height=3, wrap="word", font=("Segoe UI", 10))
        self.notes.pack(fill="x", pady=(4, 8))
        source_heading = ttk.Frame(editor)
        source_heading.pack(fill="x")
        ttk.Label(source_heading, text="Source text — paste relevant passages").pack(side="left")
        ttk.Button(source_heading, text="Load local file…", command=self.load_file).pack(side="right")
        self.text = ScrolledText(editor, height=6, wrap="word", font=("Segoe UI", 10))
        self.text.pack(fill="both", expand=True, pady=(4, 4))
        self.source_info = tk.StringVar(value="Review pasted or loaded text before saving. No website has been fetched.")
        ttk.Label(editor, textvariable=self.source_info, wraplength=590, style="Muted.TLabel").pack(anchor="w")
        self._baseline = self.values()
        self.reload()

    def values(self):
        return {"title": self.title.get(), "url": self.url.get(),
                "notes": self.notes.get("1.0", "end-1c"), "text": self.text.get("1.0", "end-1c"),
                "included": self.included.get(), **self.origin}

    def reload(self):
        self.loading = True
        try:
            self.records = job_context.list_sources(self.root, self.job_id)
            self.table.delete(*self.table.get_children())
            for item in self.records:
                self.table.insert("", "end", iid=item["id"], text=item["title"], values=("Yes" if item["included"] else "No",))
            if self.source_id and self.table.exists(self.source_id):
                self.table.selection_set(self.source_id)
        finally:
            self.loading = False

    def _can_leave(self):
        if self.values() == self._baseline:
            return True
        answer = messagebox.askyesnocancel("Unsaved reference", "Save your changes before leaving this reference?", parent=self.window)
        if answer is None:
            return False
        return self.save() if answer else True

    def _fill(self, item=None):
        item = item or {}
        self.source_id = item.get("id")
        self.title.set(item.get("title", ""))
        self.url.set(item.get("url", ""))
        self.included.set(item.get("included", True))
        self.origin = {key: item.get(key, "") for key in ("source_name", "source_sha256")}
        for widget, key in ((self.notes, "notes"), (self.text, "text")):
            widget.delete("1.0", "end")
            widget.insert("1.0", item.get(key, ""))
        self.source_info.set("Loaded from: " + self.origin["source_name"] + ". Original file unchanged." if self.origin["source_name"] else "Review pasted or loaded text before saving. No website has been fetched.")
        self._baseline = self.values()

    def select(self, _event=None):
        if self.loading:
            return
        selected = self.table.selection()
        if not selected or selected[0] == self.source_id:
            return
        selected_id = selected[0]
        if not self._can_leave():
            if self.source_id:
                self.table.selection_set(self.source_id)
            else:
                self.table.selection_remove(*selected)
            return
        item = next((entry for entry in self.records if entry["id"] == selected_id), None)
        if item:
            self._fill(item)
            self.table.selection_set(selected_id)
            self.feedback.set("Edit the text or notes, then select Save reference.")

    def new(self):
        if self._can_leave():
            self.table.selection_remove(*self.table.selection())
            self._fill()
            self.feedback.set("Add a title and a link, notes or source text, then save.")

    def save(self):
        try:
            item = job_context.save_source(self.root, self.job_id, self.values(), source_id=self.source_id)
            self._fill(item)
            self.reload()
            self.feedback.set("Saved locally. Included references are used the next time you generate documents for this job.")
            return True
        except (ValueError, OSError) as exc:
            self.feedback.set(str(exc))
            return False

    def remove(self):
        if not self.source_id:
            self.feedback.set("Select a saved reference first.")
            return
        if not messagebox.askyesno("Remove supporting reference", "Remove this reference from this job? Original documents and previously generated drafts are kept.", parent=self.window):
            return
        try:
            job_context.remove_source(self.root, self.job_id, self.source_id)
            self._fill()
            self.reload()
            self.feedback.set("Reference removed from this job.")
        except (ValueError, OSError) as exc:
            self.feedback.set(str(exc))

    def load_file(self):
        filename = filedialog.askopenfilename(parent=self.window, title="Load source text from a local document",
                                             filetypes=[("Readable documents", "*.pdf *.docx *.txt *.md *.tex")])
        if not filename:
            return
        if self.text.get("1.0", "end-1c").strip() and not messagebox.askyesno("Replace source text", "Replace the text in this form with the selected file's text? The saved reference is unchanged until you save.", parent=self.window):
            return
        try:
            extracted = job_context.extract_local_text(Path(filename))
            self.text.delete("1.0", "end")
            self.text.insert("1.0", extracted["text"])
            self.origin = {key: extracted[key] for key in ("source_name", "source_sha256")}
            if not self.title.get().strip():
                self.title.set(Path(filename).stem)
            self.source_info.set("Loaded from: " + extracted["source_name"] + ". Review the text, then save. Original file unchanged.")
            self.feedback.set("File read locally. Only the extracted text and filename will be stored; the original stays in place.")
        except (ValueError, OSError) as exc:
            self.feedback.set(str(exc))

    def close(self):
        if self._can_leave():
            self.window.destroy()
