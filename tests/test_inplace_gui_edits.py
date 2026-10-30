"""Desktop edits keep active paths stable and archived versions discoverable."""
import json
from pathlib import Path
import shutil
import socket

import pytest

from job_radar import revisions
from job_radar.gui_services import GuiService
from test_revisions import source, application, snapshot


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Unexpected network call"))


def test_repeated_improvements_and_manual_save_keep_active_folder_and_history(source, tmp_path, monkeypatch):
    root = tmp_path / "app"
    active = root / "applications" / "example_2026-10-17_abc123"
    shutil.copytree(source, active)
    before = snapshot(active)
    service = GuiService(root)
    count = []

    def response(**kwargs):
        count.append(True)
        return application().model_copy(update={"profile": "Updated research profile " + str(len(count))}), {"mock": True}

    monkeypatch.setattr(revisions, "resolve_response_fn", lambda settings: response)
    settings = {"provider": "openai", "model": "synthetic"}
    first = service.improve(active, "cv", "Clarify the profile", provider_settings=settings)
    archive1 = Path(json.loads((active / "revision.json").read_text(encoding="utf-8"))["source_dir"])
    assert snapshot(archive1) == before
    assert first["outputs"] == [active]
    assert "Updated research profile 1" in service.compare_draft(active, "cv")
    second = service.improve(active, "cv", "Clarify it again", provider_settings=settings)
    assert second["outputs"] == [active]
    assert snapshot(archive1) == before
    assert "-Updated research profile 1" in service.compare_draft(active, "cv")
    assert "+Updated research profile 2" in service.compare_draft(active, "cv")
    assert [item["directory"] for item in service.list_drafts()] == [active]
    assert len(service.list_drafts(include_history=True)) == 3
    assert len(count) == 2

    saved = service.manual_document(active, "cover_letter")
    values = {item["id"]: item["value"] for item in saved["fields"]}
    result = service.save_manual_document(active, "cover_letter", values, "Private note", saved["source_fingerprint"])
    assert result["outputs"] == [active]
    assert len(service.list_drafts()) == 1
    assert len(service.list_drafts(include_history=True)) == 4
    assert service.manual_document(active, "cover_letter")["notes"] == "Private note"
    assert len(count) == 2
    with pytest.raises(ValueError, match="read-only"):
        service.improve(archive1, "cv", "Change old version", provider_settings=settings)


def test_no_change_result_retains_active_folder_and_does_not_archive(source, tmp_path, monkeypatch):
    root = tmp_path / "app"
    active = root / "applications" / "example"
    shutil.copytree(source, active)
    before = snapshot(active)
    service = GuiService(root)

    def no_change(**kwargs):
        assert kwargs["formatting_only"] is True
        staged = kwargs["output_root"] / "attempt"
        staged.mkdir(parents=True)
        (staged / "revision.json").write_text(json.dumps({"status": "no_changes"}), encoding="utf-8")
        (staged / "REVIEW_NOTES.txt").write_text("The requested layout needs a template edit.", encoding="utf-8")
        return staged

    monkeypatch.setattr(revisions, "revise_application", no_change)
    result = service.improve(active, "cover_letter", "Change unsupported layout", formatting_only=True)
    assert result["unchanged"]
    assert result["outputs"] == [active]
    assert snapshot(active) == before
    assert not (root / "applications" / ".history").exists()
    assert len(service.list_drafts(include_history=True)) == 1
    assert "template edit" in result["review_notes"][0]
