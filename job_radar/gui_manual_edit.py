"""Plain-text editing and private notes for a selected application version."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText


class ManualEditView:
    def open_manual_editor(self):
        try:
            source = Path(self._chosen_draft()["directory"])
            document = self.review_document.get()
            saved = self.service.manual_document(source, document)
        except Exception as exc:
            return self._error(exc)
        window = tk.Toplevel(self.window)
        window.title("Edit CV text and notes" if document == "cv" else "Edit cover letter text and notes")
        window.geometry("920x700")
        window.minsize(700, 560)
        window.transient(self.window)
        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Edit the words directly", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Choose a section on the left, then edit its text. Save updates the same files and keeps the previous version in history; no AI is called.",
                  wraplength=840, style="Muted.TLabel").pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        guidance = ttk.Label(outer, text="Check any changed facts. Edits affect this saved version, not your verified master profile. "
                             "Contact details, page design and template labels are retained. Notes stay out of the generated documents.",
                             wraplength=840, style="Muted.TLabel")
        guidance.pack(side="bottom", fill="x", pady=(8, 0))
        notes_area = ttk.LabelFrame(outer, text="My notes / annotations (saved locally)", padding=8)
        notes_area.pack(side="bottom", fill="x", pady=(12, 0))
        notes = ScrolledText(notes_area, height=4, wrap="word", font=("Segoe UI", 10), undo=True)
        notes.pack(fill="x")
        notes.insert("1.0", saved["notes"])
        panels = ttk.Panedwindow(outer, orient="horizontal")
        panels.pack(fill="both", expand=True)
        left = ttk.Frame(panels)
        right = ttk.Frame(panels)
        panels.add(left, weight=1)
        panels.add(right, weight=3)
        choices = tk.Listbox(left, exportselection=False, font=("Segoe UI", 10), activestyle="none", width=30)
        scroll = ttk.Scrollbar(left, orient="vertical", command=choices.yview)
        choices.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        choices.pack(side="left", fill="both", expand=True)
        label = tk.StringVar()
        ttk.Label(right, textvariable=label, wraplength=500).pack(anchor="w", pady=(0, 6), padx=(10, 0))
        editor = ScrolledText(right, wrap="word", font=("Segoe UI", 11), undo=True, padx=10, pady=8)
        editor.pack(fill="both", expand=True, padx=(10, 0))
        values = {field["id"]: field["value"] for field in saved["fields"]}
        current = {"id": None}
        for field in saved["fields"]:
            choices.insert("end", field["label"])

        def capture():
            if current["id"] is not None:
                values[current["id"]] = editor.get("1.0", "end-1c")

        def show_field(_event=None):
            selection = choices.curselection()
            if not selection:
                return
            capture()
            field = saved["fields"][selection[0]]
            current["id"] = field["id"]
            label.set(field["label"])
            editor.delete("1.0", "end")
            editor.insert("1.0", values[field["id"]])
            editor.edit_reset()

        choices.bind("<<ListboxSelect>>", show_field)
        choices.selection_set(0)
        show_field()
        compile_pdf = tk.BooleanVar(value=self.revision_compile_pdf.get())
        ttk.Checkbutton(actions, text="Also compile PDF", variable=compile_pdf).pack(side="left")

        def save():
            if self.busy:
                return
            capture()
            captured_values = dict(values)
            captured_notes = notes.get("1.0", "end-1c")
            requested_pdf = compile_pdf.get()
            choices.configure(state="disabled")
            editor.configure(state="disabled")
            notes.configure(state="disabled")

            def failed(_error):
                if window.winfo_exists():
                    choices.configure(state="normal")
                    editor.configure(state="normal")
                    notes.configure(state="normal")

            def done(result):
                if window.winfo_exists():
                    window.destroy()
                self.last_output = Path(result["output_root"])
                self.refresh_drafts(selected=result["outputs"][0])
                self.tabs.select(self.review_tab)
                message = "Your text and notes were saved in the same folder. The previous version is in history. No model was called."
                errors = result.get("validation_errors", []) + result.get("compile_errors", [])
                if errors:
                    message += "\n" + "\n".join(map(str, errors))
                if result.get("review_notes"):
                    message += "\nReview notes:\n" + "\n".join(map(str, result["review_notes"]))
                self.revision_result.set(message)
                self.status.set("Manual version saved locally.")

            self._run("Saving your edited text locally…", lambda: self.service.save_manual_document(
                source, document, captured_values, captured_notes, saved["source_fingerprint"],
                compile_pdf=requested_pdf), done, failed)

        def close():
            if self.busy:
                return
            capture()
            changed = any(values[field["id"]] != field["value"] for field in saved["fields"])
            changed = changed or notes.get("1.0", "end-1c") != saved["notes"]
            if changed and not messagebox.askyesno("Discard unsaved edits?", "Close the editor without saving your changes?", parent=window):
                return
            window.destroy()

        self._button(actions, "Save changes", save)
        self._button(actions, "Cancel", close)
        window.protocol("WM_DELETE_WINDOW", close)
