"""Local contact library and vacancy associations."""
import tkinter as tk
import sqlite3
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import network


def _table(parent, columns):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse", height=9)
    for key, label, width in columns:
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=55)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    return tree


def _dialog(parent, title):
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    panel = ttk.Frame(win, padding=16)
    panel.pack(fill="both", expand=True)
    return win, panel


class NetworkView:
    def _build_network(self):
        panel = self.network_tab
        ttk.Label(panel, text="My Network", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Import your LinkedIn Connections.csv or add contacts yourself. Contacts and notes stay on this computer.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 10))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(0, 10))
        self._button(actions, "Import connections CSV…", self.import_network)
        self._button(actions, "Add contact…", self.edit_network_contact)
        self._button(actions, "Edit selected…", lambda: self.edit_network_contact(selected=True))
        self._button(actions, "Delete selected", self.delete_network_contact)
        self._button(actions, "Employer aliases…", self.network_aliases)
        bar = ttk.Frame(panel)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Find a contact or employer").pack(side="left", padx=(0, 8))
        self.network_query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.network_query).pack(side="left", fill="x", expand=True)
        self.network_notice = tk.StringVar()
        ttk.Label(panel, textvariable=self.network_notice, wraplength=940).pack(side="bottom", anchor="w", pady=(8, 0))
        self.contact_tree = _table(panel, [("name", "Name", 150), ("company", "Employer", 150), ("position", "Position", 200),
                                           ("strength", "Relationship", 115), ("last_interaction", "Last interaction", 110)])
        self.contact_tree.bind("<Double-1>", lambda _: self.edit_network_contact(selected=True))
        self.network_query.trace_add("write", lambda *_: self._fill_contacts())
        self.network = network.Network()
        try:
            self._reload_network()
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.network_notice.set(f"Could not read the contact library: {exc}")

    def _fill_contacts(self):
        selected = self.contact_tree.selection()
        self.contact_tree.delete(*self.contact_tree.get_children())
        query = self.network_query.get().strip().casefold()
        for key, c in self.network.contacts.items():
            values = tuple(c[k] for k in ("name", "company", "position", "strength", "last_interaction"))
            if query in " ".join(values).casefold():
                self.contact_tree.insert("", "end", iid=str(key), values=values)
        if selected and self.contact_tree.exists(selected[0]):
            self.contact_tree.selection_set(selected[0])

    def _reload_network(self, message=None):
        self.network = network.Network(self.service.root)
        self._fill_contacts()
        self.network_notice.set(message or f"{len(self.network.contacts)} contacts")
        if hasattr(self, "candidate_tree"):
            self._fill_candidates()
        if self.selected_job:
            self.job_network.set(self._network_count(self.selected_job))

    def _network_count(self, job):
        n = self.network.count(job)
        return f"My Network · {n} matching contact" + ("" if n == 1 else "s")

    def import_network(self):
        path = filedialog.askopenfilename(parent=self.window, title="Choose the extracted LinkedIn Connections.csv",
                                          filetypes=[("Connections CSV", "*.csv")])
        if path:
            def done(counts):
                self._reload_network("Import complete. " + ", ".join(f"{v} {k}" for k, v in counts.items()) + ".")
            self._run("Importing connections locally…", lambda: network.import_connections(self.service.root, path), done)

    def edit_network_contact(self, selected=False):
        contact = {}
        if selected:
            selection = self.contact_tree.selection()
            if not selection:
                return self._error(ValueError("Select a contact first."))
            try:
                self._reload_network()
                contact = self.network.contacts.get(int(selection[0]))
                if contact is None:
                    raise ValueError("The selected contact no longer exists.")
            except (OSError, sqlite3.Error, ValueError) as exc:
                return self._error(exc)
        win, panel = _dialog(self.window, "Edit contact" if selected else "Add contact")
        form = ttk.Frame(panel)
        form.pack(fill="x")
        fields = {}
        for i, (key, label) in enumerate((("name", "Name"), ("company", "Employer"), ("position", "Position"),
                                          ("url", "LinkedIn profile URL"), ("strength", "Relationship"),
                                          ("last_interaction", "Last interaction (YYYY-MM-DD)"))):
            fields[key] = self._field(form, label, i, network.STRENGTHS if key == "strength" else None)
            fields[key].set(contact.get(key, network.STRENGTHS[0] if key == "strength" else ""))
        ttk.Label(panel, text="Private notes").pack(anchor="w", pady=(8, 2))
        notes = ScrolledText(panel, height=6, width=56, wrap="word")
        notes.pack(fill="both", expand=True)
        notes.insert("1.0", contact.get("notes", ""))
        def save():
            raw = {k: v.get() for k, v in fields.items()}
            raw["notes"] = notes.get("1.0", "end-1c")
            try:
                key = network.save_contact(self.service.root, raw, contact.get("id"), expected=contact or None)
                self._reload_network("Contact saved.")
                if self.contact_tree.exists(str(key)):
                    self.contact_tree.selection_set(str(key))
                win.destroy()
            except Exception as exc:
                self._error(exc)
        ttk.Button(panel, text="Save contact", command=save).pack(side="left", pady=(8, 0))
        ttk.Button(panel, text="Cancel", command=win.destroy).pack(side="right", pady=(8, 0))

    def delete_network_contact(self):
        selection = self.contact_tree.selection()
        if not selection:
            return self._error(ValueError("Select a contact first."))
        c = self.network.contacts[int(selection[0])]
        if messagebox.askyesno("Delete contact", f"Delete {c['name']}, their notes and manual vacancy links?", parent=self.window):
            try:
                network.delete_contact(self.service.root, c["id"])
                self._reload_network("Contact deleted.")
            except Exception as exc:
                self._error(exc)

    def network_aliases(self):
        win, panel = _dialog(self.window, "Employer aliases and subsidiaries")
        ttk.Label(panel, text="Group a subsidiary or alternative name with the employer used in vacancies.").pack(anchor="w")
        form = ttk.Frame(panel)
        form.pack(fill="x", pady=8)
        alias = self._field(form, "Alias or subsidiary", 0)
        parent = self._field(form, "Employer group", 1)
        tree = _table(panel, [("alias", "Alias", 210), ("parent", "Group", 210)])
        def fill():
            tree.delete(*tree.get_children())
            for a, p in self.network.aliases.items():
                tree.insert("", "end", iid=a, values=(a, p))
        def change(remove=False):
            try:
                if remove:
                    if not tree.selection():
                        raise ValueError("Select an alias first.")
                    network.remove_alias(self.service.root, tree.selection()[0])
                else:
                    network.save_alias(self.service.root, alias.get(), parent.get())
                self._reload_network("Employer matches updated.")
                fill()
            except Exception as exc:
                self._error(exc)
        fill()
        ttk.Button(panel, text="Save alias", command=change).pack(side="left")
        ttk.Button(panel, text="Remove selected", command=lambda: change(True)).pack(side="left")
        ttk.Button(panel, text="Done", command=win.destroy).pack(side="right")

    def network_for_job(self, *, feed=False):
        try:
            job = self._candidate() if feed else self.selected_job
            if not job:
                raise ValueError("Select a vacancy first.")
        except (StopIteration, ValueError) as exc:
            return self._error(exc)
        win, panel = _dialog(self.window, "Contacts for " + job["company"])
        ttk.Label(panel, text=f"{job['title']} · {job['company']}", style="Heading.TLabel").pack(anchor="w")
        query = tk.StringVar()
        ttk.Label(panel, text="Find a contact by name or employer").pack(anchor="w", pady=(8, 0))
        ttk.Entry(panel, textvariable=query).pack(fill="x", pady=(2, 8))
        tree = _table(panel, [("name", "Name", 175), ("company", "Employer", 175), ("reason", "Connection", 160)])
        def fill(*_):
            tree.delete(*tree.get_children())
            linked = self.network.links.get(network.job_key(job), set())
            matches = {c["id"] for c in self.network.matches(job)}
            ordered = sorted(self.network.contacts.values(), key=lambda c: c["id"] not in matches)
            for c in ordered:
                if query.get().casefold() in (c["name"] + " " + c["company"]).casefold():
                    reason = "Manually linked" if c["id"] in linked else "Employer match" if c["id"] in matches else ""
                    tree.insert("", "end", iid=str(c["id"]), values=(c["name"], c["company"], reason))
        def change(remove=False):
            try:
                if not tree.selection():
                    raise ValueError("Select a contact first. Add your contacts in My Network.")
                key = int(tree.selection()[0])
                if remove and key not in self.network.links.get(network.job_key(job), set()):
                    raise ValueError("Only manual associations can be removed here.")
                network.link(self.service.root, job, key, remove=remove)
                self._reload_network("Vacancy contacts updated.")
                fill()
            except Exception as exc:
                self._error(exc)
        query.trace_add("write", fill)
        fill()
        ttk.Button(panel, text="Link selected contact", command=change).pack(side="left")
        ttk.Button(panel, text="Remove manual link", command=lambda: change(True)).pack(side="left")
        ttk.Button(panel, text="Done", command=win.destroy).pack(side="right")
