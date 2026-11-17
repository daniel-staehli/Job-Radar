"""Word export controls, separate from AI writing references."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from .cv import _date_line
from .word_export import starter_template


class WordExportView:
    def open_word_export(self):
        if self.busy:
            self._error(ValueError("Wait for the current document action to finish before exporting."))
            return
        try:
            draft = self._chosen_draft()
        except Exception as exc:
            self._error(exc)
            return
        existing = getattr(self, "_word_export_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dialog = tk.Toplevel(self.window)
        self._word_export_dialog = dialog
        dialog.title("Export to Word")
        dialog.transient(self.window)
        dialog.geometry("720x650")
        dialog.minsize(640, 610)
        panel = ttk.Frame(dialog, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Choose a layout for each document", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(panel, text=draft.get("label", Path(draft["directory"]).name), wraplength=660).pack(anchor="w", pady=(4, 12))
        ttk.Label(panel, text="Uses the saved application wording. Export is local and uses no AI.\nWord layouts are separate from reference CVs and letters.", wraplength=650).pack(anchor="w", pady=(0, 12))
        choices, enabled, options = {}, {}, {}
        status = tk.StringVar(value="Choose layouts, then export. Open the Word files to check the pages before sending.")
        results = []

        def refresh(kind, selected=None):
            options[kind] = self.service.word_layouts(kind)
            choices[kind]["values"] = [label for _, label in options[kind]]
            index = next((i for i, (key, _) in enumerate(options[kind]) if key == selected), 0)
            choices[kind].current(index)

        def import_layout(kind):
            source = filedialog.askopenfilename(parent=dialog, title="Import a Word layout template", filetypes=[("Word template", "*.docx")])
            if not source:
                return
            try:
                identity = self.service.import_word_layout(Path(source), kind)
                refresh(kind, identity)
                status.set("Layout imported and selected. It is stored separately from writing references.")
            except Exception as exc:
                self._error(exc)

        def save_starter(kind):
            selected = choices[kind].current()
            layout = options[kind][selected][0] if selected >= 0 else "clean"
            if layout not in {"clean", "classic"}:
                layout = "clean"
            target = filedialog.asksaveasfilename(parent=dialog, title="Save a starter template to customise in Word",
                initialfile=f"{kind}_{layout}_template.docx", defaultextension=".docx", filetypes=[("Word document", "*.docx")])
            if not target:
                return
            try:
                # The file dialog explicitly asks before replacing an existing file.
                Path(target).write_bytes(starter_template(kind, layout))
                status.set("Starter saved. Edit its formatting in Word, keep the placeholders, then import it here.")
                self.open_path(Path(target))
            except Exception as exc:
                self._error(exc)

        for kind, label in (("cv", "CV"), ("cover_letter", "Cover letter")):
            box = ttk.LabelFrame(panel, text=label, padding=10)
            box.pack(fill="x", pady=(0, 10))
            row = ttk.Frame(box)
            row.pack(fill="x")
            enabled[kind] = tk.BooleanVar(value=True)
            ttk.Checkbutton(row, text="Export", variable=enabled[kind]).pack(side="left", padx=(0, 10))
            choices[kind] = ttk.Combobox(row, state="readonly")
            choices[kind].pack(side="left", fill="x", expand=True)
            refresh(kind)
            actions = ttk.Frame(box)
            actions.pack(fill="x", pady=(8, 0))
            ttk.Button(actions, text="Save starter template…", command=lambda k=kind: save_starter(k)).pack(side="left", padx=(0, 8))
            ttk.Button(actions, text="Import template…", command=lambda k=kind: import_layout(k)).pack(side="left")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="Letter date (editable; blank to omit)").pack(side="left", padx=(0, 10))
        date = tk.StringVar(value=_date_line("English"))
        ttk.Entry(row, textvariable=date).pack(side="left", fill="x", expand=True)
        ttk.Label(panel, text="Custom templates: use the starter placeholders, each in its own paragraph. Change fonts, margins, spacing, tables and Word styles. Images, headers, footers, fields and tracked changes are not supported yet.", wraplength=650).pack(anchor="w", pady=(0, 10))
        ttk.Label(panel, text="New Word files are saved beside the application with unique names. Word edits do not sync back. These layouts do not convert LaTeX formatting, photographs or text hard-coded in a LaTeX template.", wraplength=650).pack(anchor="w", pady=(0, 10))
        ttk.Label(panel, textvariable=status, wraplength=650).pack(anchor="w", pady=(0, 10))

        def export():
            if self.busy:
                self._error(ValueError("Wait for the current document action to finish before exporting."))
                return
            try:
                selections = {kind: options[kind][choice.current()][0] for kind, choice in choices.items() if enabled[kind].get()}
                paths = self.service.export_word(draft["directory"], selections, date.get().strip())
                results[:] = paths
                self.last_output = Path(draft["directory"])
                status.set(f"Exported {len(paths)} Word document(s). Your existing files are unchanged. Use Open Word files to check formatting.")
                open_button.configure(state="normal")
                folder_button.configure(state="normal")
            except Exception as exc:
                self._error(exc)

        actions = ttk.Frame(panel)
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="Export Word files", command=export, style="Primary.TButton").pack(side="left", padx=(0, 8))
        open_button = ttk.Button(actions, text="Open Word files", state="disabled", command=lambda: [self.open_path(path) for path in results])
        open_button.pack(side="left", padx=(0, 8))
        folder_button = ttk.Button(actions, text="Open folder", state="disabled", command=lambda: self.open_path(Path(draft["directory"])))
        folder_button.pack(side="left")
        ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right")
