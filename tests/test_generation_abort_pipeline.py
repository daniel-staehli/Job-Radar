"""Real local generation pipeline checks with fictional inputs and no provider calls."""
from pathlib import Path

import pytest

from job_radar import cv
from job_radar.cancellation import CancellationToken, GenerationCancelled, cancellation_scope, current_token
from job_radar.config import ROOT
from job_radar.db import JobDatabase
from job_radar.generation_state import ABORTED, FAILED, IN_PROGRESS, finish_outputs, mark_incomplete
from job_radar.gui_services import GuiService
from test_cv import job, tailoring


@pytest.fixture
def arguments(tmp_path):
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    job_id, _ = db.upsert_job(job())
    return dict(db=db, selection_path=tmp_path / 'unused.yaml', job_ids_override=[job_id],
                profile_path=ROOT / 'examples/master_profile.yaml',
                template_path=ROOT / 'templates/cv_default.tex.j2',
                output_root=tmp_path / 'applications', dry_run=False, compile_pdf=False,
                new_run=True, offline=True)


def good_response(**kwargs):
    return tailoring(), {'synthetic': True}


def snapshot(folder):
    return {path.relative_to(folder): path.read_bytes() for path in folder.rglob('*') if path.is_file()}


def run_with_abort_markers(arguments, token, response=good_response):
    with cancellation_scope(token):
        try:
            return cv.generate_cvs(**arguments, response_fn=response)
        except GenerationCancelled:
            mark_incomplete(token, aborted=True)
            raise


def test_pre_cancelled_run_creates_no_output_and_calls_no_model(arguments):
    token = CancellationToken()
    token.cancel()
    calls = []
    with pytest.raises(GenerationCancelled):
        run_with_abort_markers(arguments, token, lambda **kwargs: calls.append(kwargs))
    assert calls == []
    assert token.outputs == ()
    assert not arguments['output_root'].exists()
    assert current_token() is None


def test_abort_when_response_arrives_keeps_inputs_but_never_writes_drafts(arguments, tmp_path):
    token = CancellationToken()
    calls = []
    def response(**kwargs):
        calls.append(kwargs)
        token.cancel()
        return good_response(**kwargs)
    with pytest.raises(GenerationCancelled):
        run_with_abort_markers(arguments, token, response)
    assert len(calls) == 1
    assert len(token.outputs) == 1
    folder = token.outputs[0]
    assert (folder / 'prompt.json').is_file()
    assert (folder / 'job_description.md').is_file()
    assert (folder / ABORTED).is_file()
    assert not (folder / IN_PROGRESS).exists()
    assert not (folder / 'cv.tex').exists()
    assert not (folder / 'motivation_letter.tex').exists()
    assert not (folder / 'tailoring.json').exists()
    assert GuiService(tmp_path).list_drafts() == []
    with pytest.raises(ValueError, match='incomplete|aborted'):
        GuiService(tmp_path).draft_text(folder, 'cv')


def test_abort_during_cv_compilation_prevents_letter_compilation_and_preserves_previous(arguments, tmp_path, monkeypatch):
    previous = cv.generate_cvs(**arguments, response_fn=good_response)[0]
    before = snapshot(previous)
    token = CancellationToken()
    compilations = []
    def compile_one(directory, tex_name):
        compilations.append(tex_name)
        (directory / 'cv.pdf').write_bytes(b'partial synthetic PDF; never display as finished')
        token.cancel()
        return True, 'Synthetic compile completed as user aborted'
    monkeypatch.setattr(cv, 'compile_latex', compile_one)
    with pytest.raises(GenerationCancelled):
        run_with_abort_markers({**arguments, 'compile_pdf': True}, token)
    assert compilations == ['cv.tex']
    folder = token.outputs[0]
    assert folder != previous
    assert (folder / 'cv.tex').is_file() and (folder / 'motivation_letter.tex').is_file()
    assert (folder / 'cv.pdf').is_file()
    assert not (folder / 'motivation_letter.pdf').exists()
    assert (folder / ABORTED).is_file()
    assert snapshot(previous) == before
    drafts = GuiService(tmp_path).list_drafts()
    assert [item['directory'] for item in drafts] == [previous]


def test_new_run_after_abort_finishes_only_when_completion_is_accepted(arguments, tmp_path):
    stopped = CancellationToken()
    def cancel_response(**kwargs):
        stopped.cancel()
        return good_response(**kwargs)
    with pytest.raises(GenerationCancelled):
        run_with_abort_markers(arguments, stopped, cancel_response)
    before = snapshot(stopped.outputs[0])
    fresh = CancellationToken()
    outputs = run_with_abort_markers(arguments, fresh)
    assert len(outputs) == 1
    folder = outputs[0]
    assert tuple(outputs) == fresh.outputs
    assert folder != stopped.outputs[0]
    assert (folder / IN_PROGRESS).is_file()
    assert not (folder / ABORTED).exists()
    service = GuiService(tmp_path)
    assert service.list_drafts() == []
    finish_outputs(fresh)
    assert not (folder / IN_PROGRESS).exists()
    assert [item['directory'] for item in service.list_drafts()] == [folder]
    assert snapshot(stopped.outputs[0]) == before


def test_abort_after_pipeline_return_cannot_publish_completed_files(arguments, tmp_path):
    token = CancellationToken()
    folder = run_with_abort_markers(arguments, token)[0]
    assert (folder / 'cv.tex').is_file()
    assert (folder / 'motivation_letter.tex').is_file()
    token.cancel()
    with pytest.raises(GenerationCancelled):
        finish_outputs(token)
    assert (folder / IN_PROGRESS).is_file()
    mark_incomplete(token, aborted=True)
    assert (folder / ABORTED).is_file()
    assert not (folder / IN_PROGRESS).exists()
    assert GuiService(tmp_path).list_drafts() == []


def test_failed_generation_marker_keeps_partial_pair_out_of_library(arguments, tmp_path):
    token = CancellationToken()
    folder = run_with_abort_markers(arguments, token)[0]
    mark_incomplete(token, aborted=False)
    assert (folder / FAILED).is_file()
    assert not (folder / IN_PROGRESS).exists()
    assert not (folder / ABORTED).exists()
    assert (folder / 'cv.tex').is_file()
    assert GuiService(tmp_path).list_drafts() == []


def test_unscoped_generation_and_local_preview_do_not_add_progress_markers(arguments, tmp_path):
    complete = cv.generate_cvs(**arguments, response_fn=good_response)[0]
    assert not any((complete / name).exists() for name in (ABORTED, FAILED, IN_PROGRESS))
    token = CancellationToken()
    calls = []
    with cancellation_scope(token):
        preview = cv.generate_cvs(**{**arguments, 'dry_run': True},
                                 response_fn=lambda **kwargs: calls.append(kwargs))[0]
    assert calls == []
    assert token.outputs == ()
    assert (preview / 'generation_preview.json').is_file()
    assert not any((preview / name).exists() for name in (ABORTED, FAILED, IN_PROGRESS))
    assert [item['directory'] for item in GuiService(tmp_path).list_drafts()] == [complete]


def test_cancellation_before_pdf_process_skips_tool_launch(tmp_path, monkeypatch):
    token = CancellationToken()
    calls = []
    monkeypatch.setattr(cv.shutil, 'which', lambda name: calls.append(name) or 'synthetic-latexmk')
    with cancellation_scope(token):
        token.cancel()
        with pytest.raises(GenerationCancelled):
            cv.compile_latex(tmp_path)
    assert calls == []
