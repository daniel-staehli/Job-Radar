"""Exercise the local contact library with fictional exports and vacancies."""
import csv
import tkinter as tk
from tkinter import ttk

import pytest

from job_radar import network
from job_radar.job_match import _job_snapshot
from test_gui import app, finish, save_through_form


def export(path, *rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows([["Notes:"], [], ["First Name", "Last Name", "URL", "Email Address",
                                          "Company", "Position", "Connected On"]])
        for url, company, position in rows:
            writer.writerow(["Alex", "Example", url, "fictional@example.invalid", company, position, "01 Jan 2024"])
    return path


def test_import_updates_employer_preserves_contact_and_private_annotations(tmp_path):
    root = tmp_path / "workspace"
    assert not network.Network(root).contacts
    assert not root.exists()
    path = export(tmp_path / "Connections.csv",
                  ("https://ch.linkedin.com/in/Alex-example/?tracking=1", "Example Labs AG", "Lead, R&D"))
    assert network.import_connections(root, path)["added"] == 1
    old = next(iter(network.Network(root).contacts.values()))
    assert old["url"] == "https://www.linkedin.com/in/alex-example"
    assert old["last_interaction"] == ""
    network.save_contact(root, dict(old, notes="Private annotation", strength="Colleague",
                                    last_interaction="2026-09-01"), old["id"])
    job = {"job_id": "job_example", "company": "Third company"}
    network.link(root, job, old["id"])
    export(path, (old["url"], "Another Employer S.A.", ""))
    assert network.import_connections(root, path)["updated"] == 1
    assert network.import_connections(root, path)["unchanged"] == 1
    view = network.Network(root)
    assert view.count({"company": "Example Labs AG"}) == 0
    current, = view.matches({"company": "Another Employer"})
    assert (current["id"], current["position"], current["notes"], current["strength"], current["last_interaction"]) == (
        old["id"], "Lead, R&D", "Private annotation", "Colleague", "2026-09-01")
    assert view.matches(job) == [current]
    assert list(root.rglob("*.csv")) == []
    assert b"fictional@example.invalid" not in (root / "data" / "network.sqlite3").read_bytes()


def test_repeat_import_name_fallback_and_distinct_people(tmp_path):
    path = export(tmp_path / "Contacts.csv", ("", "Éxample Labs A.G.", "Scientist"))
    assert network.import_connections(tmp_path, path)["added"] == 1
    export(path, ("linkedin.com/in/alex-one", "Example Labs", "Lead"))
    assert network.import_connections(tmp_path, path)["updated"] == 1
    export(path, ("linkedin.com/in/alex-two", "Example Labs", "Director"))
    assert network.import_connections(tmp_path, path)["added"] == 1
    export(path, ("", "Example Labs", "Ambiguous"), ("", "", "No employer"), ("example.invalid", "X", "Invalid URL"))
    assert network.import_connections(tmp_path, path) == dict(added=0, updated=0, unchanged=0, skipped=3)
    assert len(network.Network(tmp_path).contacts) == 2


def test_import_is_atomic_on_malformed_export(tmp_path):
    path = export(tmp_path / "Connections.csv", ("", "Example", "Analyst"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write('"unterminated')
    with pytest.raises(csv.Error):
        network.import_connections(tmp_path, path)
    assert not network.Network(tmp_path).contacts
    path.write_text("unexpected,columns\n", encoding="utf-8")
    with pytest.raises(ValueError, match="headers"):
        network.import_connections(tmp_path, path)


def test_manual_edits_validation_and_stale_record(tmp_path):
    raw = dict(name="Sam Example", url="linkedin.com/in/sam-example")
    key = network.save_contact(tmp_path, raw)
    with pytest.raises(ValueError, match="already"):
        network.save_contact(tmp_path, raw)
    with pytest.raises(ValueError):
        network.save_contact(tmp_path, dict(raw, last_interaction="2026-02-30"), key)
    prior = network.Network(tmp_path).contacts[key]
    network.save_contact(tmp_path, dict(prior, notes="Saved first"), key, expected=prior)
    with pytest.raises(ValueError, match="changed"):
        network.save_contact(tmp_path, dict(prior, notes="Stale edit"), key, expected=prior)
    assert network.Network(tmp_path).contacts[key]["notes"] == "Saved first"


def test_subsidiaries_priority_and_saved_job_links(tmp_path):
    ana = network.save_contact(tmp_path, dict(name="Ana Example", company="Example Unit", strength="Colleague"))
    sam = network.save_contact(tmp_path, dict(name="Sam Example", company="Group AG", strength="Acquaintance"))
    network.save_alias(tmp_path, "Example Unit", "Regional Group SA")
    network.save_alias(tmp_path, "Regional Group", "Group")
    with pytest.raises(ValueError, match="loop"):
        network.save_alias(tmp_path, "Group", "Example Unit")
    group_job = {"company": "Group"}
    view = network.Network(tmp_path)
    assert [c["id"] for c in view.matches(group_job)] == [ana, sam]
    assert view.priority(group_job) > view.priority({"company": "Unknown"})
    network.remove_alias(tmp_path, "Regional Group")
    assert network.Network(tmp_path).count(group_job) == 1
    candidate = {"id": "candidate_example", "company": "Other"}
    saved = {"job_id": "job_ext_example", "company": "Other"}
    network.link(tmp_path, candidate, ana)
    network.link(tmp_path, saved, ana)
    assert network.Network(tmp_path).count(saved) == 1
    network.link(tmp_path, saved, ana, remove=True)
    assert network.Network(tmp_path).count(candidate) == 0
    network.link(tmp_path, saved, ana)
    network.delete_contact(tmp_path, ana)
    assert not network.Network(tmp_path).links


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_desktop_import_ranking_manual_association_and_privacy(app, tmp_path, monkeypatch):
    path = export(tmp_path / "Connections.csv", ("", "Example Labs AG", "Scientist"))
    monkeypatch.setattr("job_radar.gui_network.filedialog.askopenfilename", lambda **kw: str(path))
    app.import_network()
    finish(app)
    assert not app.test_errors and "1 added" in app.network_notice.get()
    contact, = app.contact_tree.get_children()
    app.age_filter.set("All ages")
    app.candidates = [dict(id="candidate_" + str(i), company=company, title="Lead", location="Basel")
                      for i, company in enumerate(("Elsewhere", "Example Labs"))]
    app._fill_candidates()
    assert app.candidate_tree.get_children() == ("candidate_1", "candidate_0")
    job_id = save_through_form(app, company="Different Employer")
    app.network_for_job()
    dialog, = [w for w in app.window.winfo_children() if isinstance(w, tk.Toplevel)]
    widgets = list(descendants(dialog))
    tree, = [w for w in widgets if isinstance(w, ttk.Treeview)]
    tree.selection_set(contact)
    next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Link selected contact").invoke()
    assert "1 matching contact" in app.job_network.get()
    dialog.destroy()
    raw = app.service.jobs()[0]
    assert raw["job_id"] == job_id
    assert "Alex Example" in app.network.summary(raw)
    assert "Alex Example" not in repr(raw) + repr(_job_snapshot(raw))
    app.contact_tree.selection_set(contact)
    monkeypatch.setattr("job_radar.gui_network.messagebox.askyesno", lambda *args, **kw: True)
    app.delete_network_contact()
    assert "0 matching contacts" in app.job_network.get()
    assert app.service.jobs()[0]["job_id"] == job_id
    assert not app.test_errors


def test_desktop_add_and_edit_contact(app):
    for existing in (False, True):
        app.edit_network_contact(selected=existing)
        dialog, = [w for w in app.window.winfo_children() if isinstance(w, tk.Toplevel)]
        widgets = list(descendants(dialog))
        if not existing:
            name = next(w for w in widgets if isinstance(w, ttk.Entry))
            name.insert(0, "Sam Example")
        notes = next(w for w in widgets if isinstance(w, tk.Text))
        notes.delete("1.0", "end")
        notes.insert("1.0", "Updated note" if existing else "First note")
        next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Save contact").invoke()
        assert not app.test_errors
        assert len(app.contact_tree.selection()) == 1
    record, = network.Network(app.service.root).contacts.values()
    assert (record["name"], record["notes"]) == ("Sam Example", "Updated note")
