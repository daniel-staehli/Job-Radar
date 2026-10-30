"""Check desktop opening without a Tk display or real external applications."""
from types import SimpleNamespace

import pytest

from job_radar import gui


@pytest.fixture
def opener(monkeypatch):
    app = object.__new__(gui.JobRadarApp)
    errors, launches = [], []
    app._error = errors.append
    monkeypatch.setattr(gui.os, "startfile", lambda path: launches.append(("startfile", path)),
                        raising=False)
    monkeypatch.setattr(gui.subprocess, "Popen",
                        lambda args, **kwargs: launches.append(("process", args, kwargs)))
    return app, errors, launches


@pytest.mark.parametrize("platform,command", [("win32", None), ("darwin", "open"), ("linux", "xdg-open")])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_open_path_uses_platform_opener_and_literal_path(tmp_path, monkeypatch, opener,
                                                        platform, command, kind):
    app, errors, launches = opener
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform=platform))
    path = tmp_path / "Draft $(echo example); & 'notes' [1]"
    if kind == "directory":
        path.mkdir()
    else:
        path.write_text("Synthetic document", encoding="utf-8")

    app.open_path(path)

    resolved = str(path.resolve())
    expected = ("startfile", resolved) if command is None else (
        "process", [command, resolved], {"shell": False}
    )
    assert launches == [expected]
    assert errors == []


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_open_path_reports_missing_target_without_launch(tmp_path, monkeypatch, opener, platform):
    app, errors, launches = opener
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform=platform))

    app.open_path(tmp_path / "missing")

    assert launches == []
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert str(errors[0]) == "This folder or file has not been created yet."


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_open_path_reports_launch_failure(tmp_path, monkeypatch, opener, platform, error_type):
    app, errors, launches = opener
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform=platform))
    failure = error_type("The desktop opener is unavailable")

    def fail(*args, **kwargs):
        raise failure

    if platform == "win32":
        monkeypatch.setattr(gui.os, "startfile", fail)
    else:
        monkeypatch.setattr(gui.subprocess, "Popen", fail)

    app.open_path(tmp_path)

    assert errors == [failure]
    assert launches == []


def test_open_path_reports_unavailable_windows_api(tmp_path, monkeypatch, opener):
    app, errors, launches = opener
    monkeypatch.setattr(gui, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delattr(gui.os, "startfile")

    app.open_path(tmp_path)

    assert launches == []
    assert len(errors) == 1
    assert isinstance(errors[0], AttributeError)
