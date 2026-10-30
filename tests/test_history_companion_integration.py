"""In-place editing preserves and protects an externally edited companion."""
import json
from pathlib import Path
import shutil

import pytest

from job_radar import revisions
from job_radar.gui_services import GuiService
from test_revisions import source, application, snapshot


@pytest.mark.parametrize("operation", ["improve", "manual"])
@pytest.mark.parametrize("document", ["cv", "cover_letter"])
def test_inplace_edit_preserves_external_companion_and_blocks_later_overwrite(source, tmp_path, monkeypatch,
                                                                            operation, document):
    active = tmp_path / "app" / "applications" / "example"
    shutil.copytree(source, active)
    service = GuiService(tmp_path / "app")
    companion = "cover_letter" if document == "cv" else "cv"
    companion_file = "motivation_letter.tex" if companion == "cover_letter" else "cv.tex"
    original_manifest = json.loads((active / "document_hashes.json").read_text())
    external = b"Manually adjusted LaTeX outside Job Radar\r\n"
    (active / companion_file).write_bytes(external)
    before = snapshot(active)
    calls = []

    def respond(**kwargs):
        calls.append(kwargs)
        proposal = application().model_copy(deep=True)
        proposal.profile = "Researcher focused on careful experiment coordination."
        proposal.motivation_letter.subject = "Updated research application"
        return proposal, {"mock": True}

    monkeypatch.setattr(revisions, "resolve_response_fn", lambda settings: respond)
    settings = {"provider": "openai", "model": "synthetic"}
    if operation == "improve":
        result = service.improve(active, document, "Clarify selected document", provider_settings=settings)
    else:
        editable = service.manual_document(active, document)
        values = {field["id"]: field["value"] for field in editable["fields"]}
        result = service.save_manual_document(active, document, values, "New private note",
                                              editable["source_fingerprint"])
    assert result["outputs"] == [active]
    assert (active / companion_file).read_bytes() == external
    assert json.loads((active / "document_hashes.json").read_text())[companion_file] == original_manifest[companion_file]
    archive = Path(json.loads((active / "revision.json").read_text())["source_dir"])
    assert snapshot(archive) == before
    prior_call_count = len(calls)
    with pytest.raises(ValueError, match="manually edited|manually changed"):
        service.improve(active, companion, "Rewrite external edit", provider_settings=settings)
    with pytest.raises(ValueError, match="changed outside"):
        service.manual_document(active, companion)
    assert len(calls) == prior_call_count
