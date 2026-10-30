"""Missing optional reply fields do not undo a saved recipient-block choice."""
import json

import pytest

from job_radar.models import TailoredApplication
from job_radar.revisions import revise_application
from test_letter_layout import make_layout_source, run_layout_revision
from test_revisions import source, snapshot


@pytest.mark.parametrize("formatting_only", [True, False])
@pytest.mark.parametrize("parsed", [True, False])
def test_omitted_layout_setting_preserves_hidden_recipient(source, tmp_path, formatting_only, parsed):
    make_layout_source(source)
    hidden = run_layout_revision(source, tmp_path)
    before = snapshot(hidden)
    proposal = json.loads((hidden / "tailoring.json").read_text())
    proposal["motivation_letter"].pop("show_recipient_block")
    proposal["motivation_letter"]["subject"] = "A clarified research application"
    if parsed:
        proposal = TailoredApplication.model_validate(proposal)
    revised = revise_application(
        source_dir=hidden, feedback="Use larger margins" if formatting_only else "Clarify the subject",
        document="cover_letter", formatting_only=formatting_only, output_root=tmp_path / "revision",
        response_fn=lambda **kw: (proposal, {"mock": True}),
    )
    assert snapshot(hidden) == before
    if formatting_only:
        assert json.loads((revised / "revision.json").read_text())["status"] == "no_changes"
        assert not (revised / "motivation_letter.tex").exists()
    else:
        updated = TailoredApplication.model_validate_json((revised / "tailoring.json").read_text())
        assert updated.motivation_letter.show_recipient_block is False
        assert updated.motivation_letter.subject == "A clarified research application"
        assert b"\\textbf{ExampleWorks}" not in (revised / "motivation_letter.tex").read_bytes()
