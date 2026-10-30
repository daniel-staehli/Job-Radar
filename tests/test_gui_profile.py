"""Profile controls persist deliberate edits and require review of CV suggestions."""
from copy import deepcopy
import time

from job_radar import profile
from job_radar.gui_profile import ProfileSuggestionsDialog
from test_gui import app, finish


def seed_profile(app):
    value = profile.empty_profile()
    value["contact"]["name"] = "Test Candidate"
    value["contact"]["custom_field"] = "keep contact extension"
    value["candidate_summary"] = "Scientist coordinating experiments."
    value["experiences"] = [{"key": "lab", "role": "Scientist", "dates": "2020-2024", "location": "Test City", "subtitle": "Lab",
                              "custom_field": "keep role extension", "bullets": [{"id": "fact_plan", "text": "Planned experiments.", "evidence": "keep source"}]}]
    value["search_preferences"] = {"role_families": ["Research"], "custom_filter": True}
    value["private_extension"] = {"keep": True}
    saved = profile.save_profile(app.service.root, value, "missing")
    app._fill_profile(saved)
    return value


def test_manual_profile_edit_survives_reload_and_preserves_unknown_fields(app):
    original = seed_profile(app)
    app.profile_contact["name"].set("Updated Candidate")
    app.profile_summary.delete("1.0", "end")
    app.profile_summary.insert("1.0", "Updated, reviewed summary.")
    assert app._profile_has_unsaved_changes()
    assert app.save_profile()
    assert not app._profile_has_unsaved_changes()
    saved = profile.load_profile(app.service.root)["profile"]
    assert saved["contact"]["name"] == "Updated Candidate"
    assert saved["candidate_summary"] == "Updated, reviewed summary."
    assert saved["private_extension"] == original["private_extension"]
    assert saved["experiences"] == original["experiences"]
    assert saved["contact"]["custom_field"] == "keep contact extension"
    assert saved["search_preferences"]["custom_filter"] is True
    assert app._load_profile_saved()
    assert app.profile_summary.get("1.0", "end-1c") == saved["candidate_summary"]


def test_new_skill_entry_generates_identifier_and_can_be_saved(app):
    seed_profile(app)
    collection = app.profile_collections["skills"]
    dialog = collection.add()
    dialog.fields["label"].insert(0, "Data analysis")
    dialog.fields["text"].insert("1.0", "Analysed scientific images.")
    assert dialog.save()
    assert app._profile_has_unsaved_changes()
    assert not profile.load_profile(app.service.root)["profile"]["skills"]
    assert app.save_profile()
    skill = profile.load_profile(app.service.root)["profile"]["skills"][0]
    assert skill["id"].startswith("manual_")
    assert skill["label"] == "Data analysis"
    assert skill["text"] == "Analysed scientific images."


def test_experience_editor_preserves_ids_and_hidden_fields(app):
    seed_profile(app)
    collection = app.profile_collections["experiences"]
    collection.table.selection_set("0")
    dialog = collection.edit()
    dialog.fields["role"].delete(0, "end")
    dialog.fields["role"].insert(0, "Senior scientist")
    dialog.bullets.table.selection_set("0")
    bullet_dialog = dialog.bullets.edit()
    bullet_dialog.fields["text"].delete("1.0", "end")
    bullet_dialog.fields["text"].insert("1.0", "Planned and coordinated experiments.")
    assert bullet_dialog.save()
    assert dialog.window.grab_current() is dialog.window
    assert dialog.save()
    assert app.save_profile()
    saved = profile.load_profile(app.service.root)["profile"]["experiences"][0]
    assert saved["key"] == "lab"
    assert saved["role"] == "Senior scientist"
    assert saved["custom_field"] == "keep role extension"
    assert saved["bullets"] == [{"id": "fact_plan", "text": "Planned and coordinated experiments.", "evidence": "keep source"}]


def test_new_experience_has_required_empty_fields(app):
    seed_profile(app)
    dialog = app.profile_collections["experiences"].add()
    dialog.fields["role"].insert(0, "Volunteer")
    assert dialog.save()
    assert app.save_profile()
    experience = profile.load_profile(app.service.root)["profile"]["experiences"][-1]
    assert experience["role"] == "Volunteer"
    assert experience["location"] == experience["dates"] == experience["subtitle"] == ""
    assert experience["bullets"] == []


def test_reload_and_close_respect_unsaved_profile_choice(app, monkeypatch):
    seed_profile(app)
    app.profile_contact["name"].set("Unsaved candidate")
    monkeypatch.setattr("job_radar.gui_profile.messagebox.askyesnocancel", lambda *args, **kwargs: None)
    assert not app._profile_can_leave()
    app.reload_profile()
    assert app.profile_contact["name"].get() == "Unsaved candidate"
    monkeypatch.setattr("job_radar.gui_profile.messagebox.askyesnocancel", lambda *args, **kwargs: False)
    app.reload_profile()
    assert app.profile_contact["name"].get() == "Test Candidate"
    app.profile_contact["name"].set("Save on close")
    monkeypatch.setattr("job_radar.gui_profile.messagebox.askyesnocancel", lambda *args, **kwargs: True)
    assert app._profile_can_leave()
    assert profile.load_profile(app.service.root)["profile"]["contact"]["name"] == "Save on close"


def test_external_profile_change_keeps_unsaved_form_on_failed_save(app):
    seed_profile(app)
    old = profile.load_profile(app.service.root)
    changed = deepcopy(old["profile"])
    changed["contact"]["name"] = "External edit"
    profile.save_profile(app.service.root, changed, old["fingerprint"])
    app.profile_contact["name"].set("My form edit")
    assert not app.save_profile()
    assert "changed since" in app.test_errors[-1]
    assert app.profile_contact["name"].get() == "My form edit"
    assert profile.load_profile(app.service.root)["profile"]["contact"]["name"] == "External edit"


def test_bad_profile_load_does_not_replace_file(app):
    directory = app.service.root / "data"
    directory.mkdir()
    path = directory / "master_profile.yaml"
    path.write_text("[broken", encoding="utf-8")
    assert not app._load_profile_saved()
    assert "Saving is disabled" in app.profile_notice.get()
    app.profile_contact["name"].set("Do not overwrite")
    assert not app.save_profile()
    assert path.read_text(encoding="utf-8") == "[broken"


def suggestion_package():
    return {"id": "profile_test", "reference": {"label": "Example CV"}, "suggestions": [
        {"id": "first", "section": "skills", "label": "Data analysis", "value": {"id": "skill_data", "label": "Data analysis", "text": "Analysed images."},
         "source_quote": "I analysed scientific images.", "reason": "Specific experience appears in this CV."},
        {"id": "second", "section": "languages", "label": "French", "value": "French (fluent)",
         "source_quote": "Fluent French", "reason": "The CV includes a language level."}]}


def test_cv_picker_filters_letters_and_requests_selected_cv_only(app, monkeypatch):
    seed_profile(app)
    app.references = [{"id": "ref_letter", "label": "Letter", "source_name": "letter.txt", "document_type": "cover_letter"},
                      {"id": "ref_cv", "label": "CV", "source_name": "cv.txt", "document_type": "cv"}]
    app._refresh_profile_references()
    assert len(app.profile_cv_choice["values"]) == 1
    calls = []
    monkeypatch.setattr(app, "_require_provider", lambda settings: None)
    monkeypatch.setattr(profile, "suggest_from_cv", lambda *args: calls.append(args) or suggestion_package())
    app.suggest_profile_from_cv()
    finish(app)
    assert calls == [(app.service.root, "ref_cv", app.ai_current)]
    assert profile.load_profile(app.service.root)["profile"]["skills"] == []
    dialog = app.profile_suggestions_dialog
    assert not dialog.selected_ids
    assert "I analysed scientific images." in dialog.details.get("1.0", "end-1c")
    dialog.window.destroy()


def test_suggestions_require_individual_selection_and_only_accept_selected(app, monkeypatch):
    seed_profile(app)
    dialog = ProfileSuggestionsDialog(app, suggestion_package())
    calls = []
    def apply(root, package, ids):
        calls.append(ids)
        snapshot = profile.load_profile(root)
        snapshot["profile"]["skills"].append(package["suggestions"][0]["value"])
        return profile.save_profile(root, snapshot["profile"], snapshot["fingerprint"])
    monkeypatch.setattr(profile, "apply_selected_suggestions", apply)
    assert not dialog.apply()
    assert not calls
    dialog.table.selection_set("first")
    dialog.include.set(True)
    dialog.toggle()
    assert dialog.selected_ids == {"first"}
    assert dialog.apply()
    assert calls == [["first"]]
    saved = profile.load_profile(app.service.root)["profile"]
    assert saved["skills"][0]["id"] == "skill_data"
    assert saved["languages"] == []
    assert not app._profile_has_unsaved_changes()


def test_dirty_profile_blocks_ai_and_acceptance(app, monkeypatch):
    seed_profile(app)
    calls = []
    monkeypatch.setattr(profile, "suggest_from_cv", lambda *args: calls.append(args))
    app.profile_contact["name"].set("Unsaved")
    app.suggest_profile_from_cv()
    assert not calls
    assert "Save your profile" in app.test_errors[-1]
    dialog = ProfileSuggestionsDialog(app, suggestion_package())
    dialog.selected_ids.add("first")
    monkeypatch.setattr(profile, "apply_selected_suggestions", lambda *args: calls.append(args))
    assert not dialog.apply()
    assert not calls
    assert "unsaved changes" in dialog.notice.get()
    dialog.window.destroy()


def test_open_dirty_entry_blocks_main_close_and_can_be_committed(app, monkeypatch):
    seed_profile(app)
    dialog = app.profile_collections["skills"].add()
    dialog.fields["label"].insert(0, "Analysis")
    assert app._profile_has_unsaved_changes()
    monkeypatch.setattr("job_radar.gui_profile.messagebox.askyesnocancel", lambda *args, **kwargs: None)
    assert not app._profile_can_leave()
    assert dialog.window.winfo_exists()
    monkeypatch.setattr("job_radar.gui_profile.messagebox.askyesnocancel", lambda *args, **kwargs: True)
    assert app._profile_can_leave()
    assert not dialog.window.winfo_exists()
    assert profile.load_profile(app.service.root)["profile"]["skills"][0]["label"] == "Analysis"


def test_review_notes_and_truncation_warning_show_without_suggestions(app):
    package = {"reference": {"label": "Long CV", "truncated": True}, "suggestions": [],
               "notes": ["The date of this experience is ambiguous."]}
    dialog = ProfileSuggestionsDialog(app, package)
    text = dialog.review_notes.get("1.0", "end-1c")
    assert "Only part of the CV text" in text
    assert "date of this experience is ambiguous" in text
    assert "No new supported additions" in dialog.notice.get()
    assert not dialog.apply()
    dialog.window.destroy()


def test_experience_suggestion_shows_target_role_before_acceptance(app):
    seed_profile(app)
    package = suggestion_package()
    package["suggestions"] = [{"id": "bullet", "section": "experience_bullets", "target_key": "lab", "label": "New fact",
                              "value": {"id": "new_fact", "text": "Coordinated samples."},
                              "source_quote": "Coordinated samples at Test Lab.", "reason": "Relevant detail."}]
    dialog = ProfileSuggestionsDialog(app, package)
    text = dialog.details.get("1.0", "end-1c")
    assert "ADD TO EXPERIENCE" in text
    assert "Scientist" in text and "2020-2024" in text and "Test City" in text
    dialog.window.destroy()


def test_profile_controls_fit_minimum_window_size(app):
    app.window.attributes("-alpha", 0)
    app.window.geometry("960x680")
    app.tabs.select(app.profile_tab)
    app.window.deiconify()
    status_label = next(widget for widget in app.progress.master.winfo_children()
                        if widget.winfo_class() == "TLabel" and str(widget.cget("textvariable")) == str(app.status))
    required = (app.profile_cv_choice, status_label, app.progress)
    # Windows maps a native Toplevel asynchronously. A single update can return
    # before the Map/Configure events arrive, especially after many GUI tests.
    # Wait for actual child geometry; keep the bounds assertions unchanged.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.window.update()
        if all(widget.winfo_ismapped() and widget.winfo_height() > 1 for widget in required):
            break
        time.sleep(0.01)
    bottom = app.window.winfo_rooty() + app.window.winfo_height()
    for widget in required:
        assert widget.winfo_ismapped() and widget.winfo_height() > 1, f"Required control did not become visible: {widget}"
        assert widget.winfo_rooty() + widget.winfo_height() <= bottom
    for section in (0, 6):
        app.profile_sections.selection_clear(0, "end")
        app.profile_sections.selection_set(section)
        app._profile_section_changed()
        app.window.update_idletasks()
        for widget in required:
            assert widget.winfo_ismapped() and widget.winfo_height() > 1
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom
