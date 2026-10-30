"""Editable candidate facts and explicitly reviewed CV suggestions."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from uuid import uuid4

from . import profile


CONTACT_FIELDS = (("name", "Name"), ("location", "Location"), ("phone", "Phone"),
                  ("email", "Email"), ("linkedin_label", "LinkedIn label"),
                  ("linkedin_url", "LinkedIn link"))
ENTRY_FIELDS = {
    "experiences": (("role", "Role / organisation"), ("location", "Location"),
                    ("dates", "Dates"), ("subtitle", "Team or focus")),
    "education": (("institution", "Institution"), ("degree", "Degree / qualification"),
                  ("dates", "Dates"), ("detail", "Details")),
    "skills": (("label", "Skill / category"), ("text", "Description")),
    "publications": (("citation", "Citation"), ("url", "Link")),
    "additional_experience": (("role", "Role"), ("location", "Location"),
                              ("dates", "Dates"), ("text", "Description")),
    "never_claim": (("pattern", "Claim to avoid"), ("why", "Why it is not supported")),
    "bullets": (("text", "Fact / achievement"),),
}
SECTION_LABELS = {"experiences": "Experience", "education": "Education", "skills": "Skills",
                  "publications": "Publications", "additional_experience": "Other experience",
                  "never_claim": "Claims to avoid", "bullets": "Facts and achievements",
                  "experience_bullets": "Experience fact", "languages": "Languages",
                  "interests": "Interests", "allowed_headline_tags": "Headline topics"}


def entry_label(item):
    if isinstance(item, str):
        return item
    for key in ("role", "title", "label", "degree", "citation", "text", "pattern"):
        if item.get(key):
            return str(item[key])
    return "Untitled entry"


def readable_value(value, indent=0):
    """Show a proposed fact without exposing internal identifiers or YAML."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join("• " + readable_value(item, indent + 1) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key.replace('_', ' ').capitalize()}: {readable_value(item, indent + 1)}"
                         for key, item in value.items() if key not in {"id", "key"} and item)
    return str(value)


class ProfileCollection(ttk.Frame):
    """A list of editable facts; identifiers and fields not shown stay intact."""
    def __init__(self, parent, section, changed=lambda: None):
        super().__init__(parent)
        self.section, self.changed = section, changed
        self.items = []
        self.dialog = None
        buttons = ttk.Frame(self)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        for label, command in (("Add", self.add), ("Edit selected", self.edit), ("Remove selected", self.remove)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 8))
        self.table = ttk.Treeview(self, columns=("detail",), show="tree headings", selectmode="browse", height=6)
        self.table.heading("#0", text=SECTION_LABELS[section])
        self.table.column("#0", width=420, minwidth=150)
        self.table.heading("detail", text="Dates / details")
        self.table.column("detail", width=160, minwidth=80)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda _event: self.edit())

    def fill(self, items):
        self.items = deepcopy(items)
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(self.items):
            detail = item.get("dates") or item.get("institution") or item.get("label") or ""
            self.table.insert("", "end", iid=str(index), text=entry_label(item), values=(detail,))

    def _open(self, item=None, index=None):
        if self.dialog is not None and self.dialog.window.winfo_exists():
            self.dialog.window.lift()
            return self.dialog
        def accept(value):
            items = deepcopy(self.items)
            if index is None:
                items.append(value)
            else:
                items[index] = value
            self.fill(items)
            self.changed()
        self.dialog = ProfileEntryDialog(self.winfo_toplevel(), self.section, item or {}, accept)
        return self.dialog

    def add(self):
        return self._open()

    def has_pending_changes(self):
        return bool(self.dialog is not None and self.dialog.window.winfo_exists() and self.dialog.has_changes())

    def close_editor(self):
        if self.dialog is not None and self.dialog.window.winfo_exists():
            return self.dialog.close()
        return True

    def edit(self):
        selected = self.table.selection()
        if selected:
            index = int(selected[0])
            return self._open(self.items[index], index)

    def remove(self):
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        if messagebox.askyesno("Remove profile entry", "Remove this entry from the form? Save profile commits the change.",
                               parent=self.winfo_toplevel()):
            self.fill([item for i, item in enumerate(self.items) if i != index])
            self.changed()


class ProfileEntryDialog:
    def __init__(self, parent, section, item, accept):
        self.original, self.section, self.accept = deepcopy(item), section, accept
        self.window = tk.Toplevel(parent)
        self.window.title("Edit " + SECTION_LABELS[section].lower())
        self.window.geometry("760x610" if section == "experiences" else "700x470")
        self.window.minsize(580, 380)
        self.window.transient(parent)
        self._previous_grab = parent.grab_current()
        self.window.grab_set()
        self.window.bind("<Destroy>", self._restore_grab, add="+")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill="both", expand=True)
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Use in profile form", command=self.save, style="Primary.TButton").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="left", padx=(8, 0))
        self.notice = tk.StringVar(value="Changes are saved to your profile when you select Save profile.")
        ttk.Label(body, textvariable=self.notice, wraplength=650, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=(8, 0))
        form = ttk.Frame(body)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.fields = {}
        specifications = list(ENTRY_FIELDS[section])
        if section == "experiences":
            specifications += [(key, label) for key, label in (("title", "Separate title"), ("organisation", "Separate organisation")) if key in item]
        for row, (key, label) in enumerate(specifications):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
            if key in {"text", "citation", "detail", "why"}:
                widget = ScrolledText(form, height=4, wrap="word", font=("Segoe UI", 10))
                widget.insert("1.0", item.get(key, ""))
            else:
                widget = ttk.Entry(form)
                widget.insert(0, item.get(key, ""))
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.fields[key] = widget
        self.bullets = None
        if section == "experiences":
            ttk.Label(body, text="Facts and achievements — keep each one specific", style="Muted.TLabel").pack(anchor="w", pady=(10, 5))
            self.bullets = ProfileCollection(body, "bullets")
            self.bullets.fill(item.get("bullets", []))
            self.bullets.pack(fill="both", expand=True)
        self.baseline = self.values()

    def _restore_grab(self, event):
        if event.widget is self.window and self._previous_grab is not None:
            try:
                if self._previous_grab.winfo_exists():
                    self._previous_grab.grab_set()
            except tk.TclError:
                pass

    def has_changes(self):
        return self.values() != self.baseline or bool(self.bullets is not None and self.bullets.has_pending_changes())

    def values(self):
        result = deepcopy(self.original)
        for key, widget in self.fields.items():
            value = (widget.get("1.0", "end-1c") if isinstance(widget, tk.Text) else widget.get()).strip()
            result[key] = value
        if self.bullets is not None:
            result["bullets"] = deepcopy(self.bullets.items)
        return result

    def save(self):
        if self.bullets is not None and not self.bullets.close_editor():
            return False
        value = self.values()
        if not any(value.get(key) for key, _label in ENTRY_FIELDS[self.section]):
            self.notice.set("Enter at least one detail for this entry.")
            return False
        if self.section == "experiences" and not value.get("key"):
            value["key"] = "manual_" + uuid4().hex[:16]
        if self.section in {"skills", "publications", "additional_experience", "bullets"} and not value.get("id"):
            value["id"] = "manual_" + uuid4().hex[:16]
        self.accept(value)
        self.window.destroy()
        return True

    def close(self):
        if self.bullets is not None and not self.bullets.close_editor():
            return False
        if self.values() != self.baseline:
            answer = messagebox.askyesnocancel("Unsaved entry", "Use your changes in the profile form before closing this entry?", parent=self.window)
            if answer is None:
                return False
            if answer:
                return self.save()
        self.window.destroy()
        return True


class ProfileView:
    def _build_profile(self):
        self._profile_loading = True
        self._profile_load_error = None
        self._profile_document = {}
        self._profile_baseline = {}
        self._profile_fingerprint = ""
        self.profile_suggestions_dialog = None
        ttk.Label(self.profile_tab, text="Your profile, in your own words", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.profile_tab, text="Keep the facts you want Job Radar to use for applications and job-match reviews. Editing and saving here stays local.",
                  wraplength=980, style="Muted.TLabel").pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(self.profile_tab)
        actions.pack(fill="x", pady=(0, 8))
        self._button(actions, "Save profile", self.save_profile, primary=True)
        self._button(actions, "Reload saved profile", self.reload_profile)
        self.profile_notice = tk.StringVar(value="Loading your saved profile…")
        ttk.Label(self.profile_tab, textvariable=self.profile_notice, wraplength=980, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        imports = ttk.LabelFrame(self.profile_tab, text="Suggest additions from a reference CV", padding=10)
        imports.pack(side="bottom", fill="x", pady=(10, 0))
        line = ttk.Frame(imports)
        line.pack(fill="x")
        self.profile_cv_choice = ttk.Combobox(line, state="readonly")
        self.profile_cv_choice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(line, "Review CV with AI", self.suggest_profile_from_cv)
        ttk.Label(imports, text="Sends your saved profile and the selected CV text to the AI provider in AI settings. You choose which suggestions to accept; nothing is added automatically.",
                  wraplength=950, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        panes = ttk.Panedwindow(self.profile_tab, orient="horizontal", height=170)
        panes.pack(fill="both", expand=True)
        navigation = ttk.Frame(panes)
        content = ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(navigation, weight=1)
        panes.add(content, weight=5)
        self.profile_sections = tk.Listbox(navigation, exportselection=False, width=20, activestyle="none",
                                          relief="flat", font=("Segoe UI", 10), selectbackground="#173452", selectforeground="white")
        self.profile_sections.pack(fill="both", expand=True)
        self._profile_frames = []
        self.profile_contact = {}
        self.profile_collections = {}
        self.profile_text_lists = {}
        self._profile_text_widgets = []
        overview = ttk.Frame(content)
        self._profile_frames.append(overview)
        self.profile_sections.insert("end", "About you")
        overview_body = self._scrolling_frame(overview)
        overview_body.master.configure(height=140)
        fields = ttk.Frame(overview_body)
        fields.pack(fill="x")
        for row, (key, label) in enumerate(CONTACT_FIELDS):
            value = self._field(fields, label, row)
            value.trace_add("write", self._profile_edited)
            self.profile_contact[key] = value
        ttk.Label(overview_body, text="Professional summary — facts, strengths and honest limits").pack(anchor="w", pady=(10, 4))
        self.profile_summary = ScrolledText(overview_body, height=5, wrap="word", font=("Segoe UI", 10))
        self.profile_summary.pack(fill="x")
        self._profile_text_widgets.append(self.profile_summary)
        for section in ("experiences", "education", "skills", "publications", "additional_experience"):
            frame = ProfileCollection(content, section, self._profile_edited)
            self.profile_collections[section] = frame
            self._profile_frames.append(frame)
            self.profile_sections.insert("end", SECTION_LABELS[section])
        preferences = ttk.Frame(content)
        self._profile_frames.append(preferences)
        self.profile_sections.insert("end", "Languages & preferences")
        preferences_body = self._scrolling_frame(preferences)
        preferences_body.master.configure(height=140)
        for key, label in (("languages", "Languages and levels"), ("interests", "Interests"),
                           ("allowed_headline_tags", "Topics allowed in your CV headline"),
                           ("role_families", "Role types you are interested in")):
            ttk.Label(preferences_body, text=label + " — one per line").pack(anchor="w", pady=(5, 3))
            widget = ScrolledText(preferences_body, height=2, wrap="word", font=("Segoe UI", 10))
            widget.pack(fill="x")
            self.profile_text_lists[key] = widget
            self._profile_text_widgets.append(widget)
        limits = ProfileCollection(content, "never_claim", self._profile_edited)
        self.profile_collections["never_claim"] = limits
        self._profile_frames.append(limits)
        self.profile_sections.insert("end", "Claims to avoid")
        for widget in self._profile_text_widgets:
            widget.bind("<<Modified>>", self._profile_text_edited)
        self.profile_sections.bind("<<ListboxSelect>>", self._profile_section_changed)
        self.profile_sections.selection_set(0)
        self._profile_section_changed()
        self._profile_cv_records = []
        self._profile_loading = False
        self._load_profile_saved()

    def _profile_section_changed(self, _event=None):
        selected = self.profile_sections.curselection()
        if not selected:
            return
        for index, frame in enumerate(self._profile_frames):
            if index == selected[0]:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    def _profile_values(self):
        value = deepcopy(self._profile_document)
        contact = value.setdefault("contact", {})
        for key, variable in self.profile_contact.items():
            text = variable.get().strip()
            if key in contact or text:
                contact[key] = text
        summary = self.profile_summary.get("1.0", "end-1c").strip()
        if "candidate_summary" in value or summary:
            value["candidate_summary"] = summary
        for key, collection in self.profile_collections.items():
            if key in value or collection.items:
                value[key] = deepcopy(collection.items)
        for key, widget in self.profile_text_lists.items():
            lines = [line.strip() for line in widget.get("1.0", "end-1c").splitlines() if line.strip()]
            if key == "role_families":
                if lines or key in value.get("search_preferences", {}):
                    value.setdefault("search_preferences", {})[key] = lines
            elif key in value or lines:
                value[key] = lines
        return value

    def _profile_has_unsaved_changes(self):
        return not self._profile_load_error and (self._profile_values() != self._profile_baseline
               or any(collection.has_pending_changes() for collection in self.profile_collections.values()))

    def _profile_edited(self, *_args):
        if not self._profile_loading:
            self.profile_notice.set("Unsaved changes. Select Save profile to use these facts for future documents and match reviews."
                                    if self._profile_has_unsaved_changes() else "Saved profile loaded. No changes to save.")

    def _profile_text_edited(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._profile_edited()

    def _fill_profile(self, snapshot):
        self._profile_loading = True
        try:
            self._profile_load_error = None
            self._profile_document = deepcopy(snapshot["profile"])
            self._profile_fingerprint = snapshot["fingerprint"]
            for key, variable in self.profile_contact.items():
                variable.set(snapshot["profile"].get("contact", {}).get(key, ""))
            self.profile_summary.delete("1.0", "end")
            self.profile_summary.insert("1.0", snapshot["profile"].get("candidate_summary", ""))
            for key, collection in self.profile_collections.items():
                collection.fill(snapshot["profile"].get(key, []))
            for key, widget in self.profile_text_lists.items():
                values = (snapshot["profile"].get("search_preferences", {}).get(key, []) if key == "role_families"
                          else snapshot["profile"].get(key, []))
                widget.delete("1.0", "end")
                widget.insert("1.0", "\n".join(values))
            for widget in self._profile_text_widgets:
                widget.edit_modified(False)
            self._profile_baseline = self._profile_values()
        finally:
            self._profile_loading = False

    def _load_profile_saved(self):
        try:
            self._fill_profile(profile.load_profile(self.service.root))
            self.profile_notice.set("Saved facts are used for new applications and AI match reviews. Your existing documents are unchanged.")
            return True
        except Exception as exc:
            self._profile_load_error = str(exc)
            self.profile_notice.set("Could not load your profile. Saving is disabled to protect the existing file. " + str(exc))
            return False

    def save_profile(self):
        if self._profile_load_error:
            self._error(ValueError("Reload your profile successfully before saving. " + self._profile_load_error))
            return False
        if not self._close_profile_editors():
            return False
        try:
            snapshot = profile.save_profile(self.service.root, self._profile_values(), self._profile_fingerprint)
            self._fill_profile(snapshot)
            self.profile_notice.set("Profile saved locally. New documents and match reviews will use these facts.")
            return True
        except Exception as exc:
            self._error(exc)
            self.profile_notice.set("Profile was not saved. Your edits remain in this form. " + str(exc))
            return False

    def _profile_can_leave(self):
        if not self._close_profile_editors():
            return False
        if not self._profile_has_unsaved_changes():
            return True
        answer = messagebox.askyesnocancel("Unsaved profile", "Save your profile changes before leaving?", parent=self.window)
        return False if answer is None else (self.save_profile() if answer else True)

    def _close_profile_editors(self):
        return all(collection.close_editor() for collection in self.profile_collections.values())

    def reload_profile(self):
        if self._profile_can_leave():
            self._load_profile_saved()

    def _refresh_profile_references(self):
        previous = self.profile_cv_choice.current()
        previous_id = self._profile_cv_records[previous]["id"] if 0 <= previous < len(self._profile_cv_records) else None
        self._profile_cv_records = [item for item in self.references if item.get("document_type", "cv") == "cv"]
        self.profile_cv_choice.configure(values=[f"{item['label']} · {item['source_name']}" for item in self._profile_cv_records])
        if self._profile_cv_records:
            index = next((i for i, item in enumerate(self._profile_cv_records) if item["id"] == previous_id), 0)
            self.profile_cv_choice.current(index)
        else:
            self.profile_cv_choice.set("")

    def suggest_profile_from_cv(self):
        try:
            if self._profile_load_error:
                raise ValueError("Reload your profile successfully before asking for suggestions.")
            if self._profile_has_unsaved_changes():
                raise ValueError("Save your profile changes before asking for CV suggestions.")
            index = self.profile_cv_choice.current()
            if not 0 <= index < len(self._profile_cv_records):
                raise ValueError("Add a CV in Reference library, then select it here.")
            reference_id = self._profile_cv_records[index]["id"]
            settings = self._selected_ai()
        except Exception as exc:
            return self._error(exc)
        def action():
            self._require_provider(settings)
            return profile.suggest_from_cv(self.service.root, reference_id, settings)
        def done(package):
            self.profile_notice.set("CV review finished. Review each suggestion before adding it to your profile.")
            self.profile_suggestions_dialog = ProfileSuggestionsDialog(self, package)
        self._run("Reviewing your selected CV for profile additions…", action, done,
                  lambda exc: self.profile_notice.set("CV review did not finish. Your saved profile is unchanged. " + str(exc)))


class ProfileSuggestionsDialog:
    def __init__(self, app, package):
        self.app, self.package = app, package
        self.suggestions = {item["id"]: item for item in package.get("suggestions", [])}
        self.selected_ids = set()
        self.window = tk.Toplevel(app.window)
        self.window.title("Review suggested profile additions")
        self.window.geometry("960x670")
        self.window.minsize(800, 530)
        self.window.transient(app.window)
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Check the facts before adding them", style="Heading.TLabel").pack(anchor="w")
        reference = package.get("reference", {}).get("label", "Selected CV")
        ttk.Label(frame, text=f"Source: {reference}. Compare each suggestion with the CV quote. Only select facts you confirm are accurate.",
                  wraplength=900, style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        notes = list(package.get("notes", []))
        if package.get("reference", {}).get("truncated"):
            notes.insert(0, "Only part of the CV text was included in this review. The remaining text was not assessed.")
        self.review_notes = None
        if notes:
            ttk.Label(frame, text="Review notes and limitations", style="Muted.TLabel").pack(anchor="w")
            self.review_notes = ScrolledText(frame, height=3, wrap="word", font=("Segoe UI", 10))
            self.review_notes.pack(fill="x", pady=(4, 10))
            self.review_notes.insert("1.0", "\n\n".join(str(note) for note in notes))
            self.review_notes.configure(state="disabled")
        bottom = ttk.Frame(frame)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.notice = tk.StringVar(value="No suggestions selected." if self.suggestions else "No new supported additions were suggested. Your profile is unchanged.")
        ttk.Label(bottom, textvariable=self.notice, wraplength=900).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Add selected to profile", style="Primary.TButton", command=self.apply).pack(side="left")
        ttk.Button(buttons, text="Close without adding", command=self.window.destroy).pack(side="right")
        panes = ttk.Panedwindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        self.table = ttk.Treeview(left, columns=("use", "section"), show="tree headings", selectmode="browse")
        for key, label, width in (("#0", "Suggested addition", 240), ("use", "Use", 45), ("section", "Section", 100)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=40)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.include = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="I have checked this fact — include it", variable=self.include, command=self.toggle).pack(anchor="w", pady=(0, 8))
        self.details = ScrolledText(right, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.details.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_selected)
        self.table.bind("<space>", self.toggle_key)
        for key, item in self.suggestions.items():
            self.table.insert("", "end", iid=key, text=item["label"], values=("", SECTION_LABELS.get(item["section"], item["section"])))
        if self.suggestions:
            self.table.selection_set(next(iter(self.suggestions)))
            self.show_selected()

    def show_selected(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        item = self.suggestions[selected[0]]
        self.include.set(item["id"] in self.selected_ids)
        target = ""
        if item["section"] == "experience_bullets":
            role = next((role for role in self.app._profile_document.get("experiences", [])
                         if role.get("key") == item.get("target_key")), None)
            details = ({key: role[key] for key in ("role", "title", "organisation", "location", "dates", "subtitle") if role.get(key)}
                       if role else "This experience is no longer in the loaded profile. Request a fresh review.")
            target = "ADD TO EXPERIENCE\n\n" + readable_value(details) + "\n\n"
        text = (target + "PROPOSED ADDITION\n\n" + readable_value(item["value"]) + "\n\nSOURCE QUOTE\n\n" + item["source_quote"]
                + "\n\nWHY IT WAS SUGGESTED\n\n" + item.get("reason", ""))
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def toggle_key(self, _event=None):
        self.include.set(not self.include.get())
        self.toggle()
        return "break"

    def toggle(self):
        selected = self.table.selection()
        if not selected:
            return
        key = selected[0]
        self.selected_ids.add(key) if self.include.get() else self.selected_ids.discard(key)
        self.table.set(key, "use", "Yes" if self.include.get() else "")
        self.notice.set(f"{len(self.selected_ids)} selected. Add selected to profile saves only these reviewed additions.")

    def apply(self):
        if not self.selected_ids:
            self.notice.set("Select at least one suggestion you have checked, or close without adding.")
            return False
        if self.app._profile_has_unsaved_changes():
            self.notice.set("Your profile form has unsaved changes. Close this review, save those changes and request fresh suggestions.")
            return False
        try:
            selected = [key for key in self.suggestions if key in self.selected_ids]
            snapshot = profile.apply_selected_suggestions(self.app.service.root, self.package, selected)
            self.app._fill_profile(snapshot)
            self.app.profile_notice.set(f"Saved {len(selected)} reviewed additions to your profile. Unselected suggestions were not added.")
            self.window.destroy()
            return True
        except Exception as exc:
            self.notice.set("No suggestions were added. " + str(exc))
            return False
