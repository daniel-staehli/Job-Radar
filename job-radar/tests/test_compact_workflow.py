"""Compact folder names work through the real offline document workflow."""
import json
import re
import shutil
import socket

import pytest
import yaml

from job_radar import cv
from job_radar.config import ROOT, load_yaml
from job_radar.gui_services import GuiService
from test_cv import tailoring
from test_gui_services import saved_application, vacancy
from test_manual_edits import edit
from test_revisions import source, run_revision, snapshot


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline naming tests must not contact a provider")
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_new_gui_runs_are_flat_distinct_and_keep_legacy_drafts(tmp_path, monkeypatch):
    service = GuiService(tmp_path)
    legacy, _, _ = saved_application(tmp_path)
    legacy_before = snapshot(legacy)
    (tmp_path / "data").mkdir()
    profile = load_yaml(ROOT / "examples" / "master_profile.yaml")
    profile["example_only"] = False
    (tmp_path / "data" / "master_profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    shutil.copytree(ROOT / "templates", tmp_path / "templates")
    job_id, _ = service.save_job(vacancy(company="Example Laboratory", title="Scientific Solutions Specialist"))
    calls = []

    def respond(**kwargs):
        calls.append(kwargs)
        return tailoring(), {"synthetic": True}

    monkeypatch.setattr(cv, "resolve_response_fn", lambda settings: respond)
    settings = {"provider": "openai", "model": "synthetic-model"}
    preview = service.preview(job_id, [], provider_settings=settings)
    first = service.generate(job_id, [], provider_settings=settings)
    first_before = snapshot(first["outputs"][0])
    second = service.generate(job_id, [], provider_settings=settings)

    assert len(calls) == 2
    folders = [result["outputs"][0] for result in (preview, first, second)]
    assert len(set(folders)) == 3
    for result, folder in zip((preview, first, second), folders):
        assert folder.parent == tmp_path / "applications"
        assert result["output_root"] == folder
        assert re.fullmatch(r"example-laboratory_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9a-f]{6}", folder.name)
        assert result["validation_errors"] == []
    for result in (first, second):
        folder = result["outputs"][0]
        assert (folder / "cv.tex").is_file()
        assert (folder / "motivation_letter.tex").is_file()
        assert json.loads((folder / "generation_inputs.json").read_text(encoding="utf-8"))["job_id"] == job_id
    assert not (folders[0] / "cv.tex").exists()
    assert snapshot(first["outputs"][0]) == first_before
    assert snapshot(legacy) == legacy_before
    assert {item["directory"] for item in service.list_drafts()} == {legacy, folders[1], folders[2]}
    assert {item["directory"] for item in service.list_drafts(job_id)} == {folders[1], folders[2]}


def test_revisions_and_manual_edits_use_company_names_and_keep_history(source, tmp_path):
    before = snapshot(source)
    revised, calls = run_revision(source, tmp_path)
    manual = edit(revised, tmp_path)
    for folder in (revised, manual):
        assert re.fullmatch(r"example-laboratory_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9a-f]{6}", folder.name)
        assert (folder / "cv.tex").is_file()
        assert (folder / "motivation_letter.tex").is_file()
    assert len(calls) == 1
    assert json.loads((revised / "revision.json").read_text(encoding="utf-8"))["source_dir"] == str(source.resolve())
    assert json.loads((manual / "revision.json").read_text(encoding="utf-8"))["source_dir"] == str(revised.resolve())
    assert snapshot(source) == before


def test_new_run_cannot_silently_ignore_resume(tmp_path):
    with pytest.raises(ValueError, match="either a new application folder or resume"):
        cv.generate_cvs(db=None, selection_path=tmp_path / "unused",
                        profile_path=tmp_path / "unused", template_path=tmp_path / "unused",
                        output_root=tmp_path / "applications", dry_run=True, compile_pdf=False,
                        new_run=True, resume=True)
    assert list(tmp_path.iterdir()) == []
