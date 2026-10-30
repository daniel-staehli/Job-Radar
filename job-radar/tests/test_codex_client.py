import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from job_radar import codex_client


class ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


def test_codex_backend_uses_read_only_structured_exec(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_client, "codex_executable", lambda: "/usr/bin/codex")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        seen["input"] = kwargs["input"]
        seen["encoding"] = kwargs["encoding"]
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text(
            json.dumps({"answer": "grounded"}), encoding="utf-8"
        )
        return SimpleNamespace(
            returncode=0,
            stdout='{"answer":"grounded"}\n',
            stderr="",
        )

    monkeypatch.setattr(codex_client.subprocess, "run", fake_run)
    parsed, raw = codex_client.structured_response(
        schema_type=ExampleOutput,
        schema_name="example",
        system="Use only supplied facts.",
        user="Prepare the application.",
        use_web_search=False,
        artifact_dir=tmp_path,
    )

    assert parsed.answer == "grounded"
    assert raw["backend"] == "codex"
    assert "--ephemeral" in seen["command"]
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "read-only"
    assert "--ask-for-approval" not in seen["command"]
    assert 'approval_policy="never"' in seen["command"]
    assert seen["command"][-1] == "-"
    assert "Use only supplied facts." in seen["input"]
    assert seen["encoding"] == "utf-8"
    assert (tmp_path / "codex_output_schema.json").exists()
    assert (tmp_path / "codex_prompt.md").exists()


@pytest.fixture
def isolated_windows_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_RADAR_CODEX_EXECUTABLE", raising=False)
    monkeypatch.setattr(codex_client.sys, "platform", "win32")
    monkeypatch.setattr(codex_client.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local Data"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming Data"))
    return tmp_path / "Local Data" / "OpenAI" / "Codex" / "bin"


def executable_fixture(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture only; never executed")
    return path


def test_discovery_finds_desktop_helper_without_path(isolated_windows_discovery):
    helper = executable_fixture(isolated_windows_discovery / "version hash" / "codex.exe")
    assert codex_client.codex_executable() == str(helper)


def test_discovery_uses_newest_desktop_helper(isolated_windows_discovery):
    older = executable_fixture(isolated_windows_discovery / "older" / "codex.exe")
    newer = executable_fixture(isolated_windows_discovery / "newer" / "codex.exe")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_710_000_000, 1_710_000_000))
    executable_fixture(isolated_windows_discovery / "newest" / "unrelated.exe")
    assert codex_client.codex_executable() == str(newer)


def test_discovery_supports_direct_install_and_respects_path(
    isolated_windows_discovery, monkeypatch
):
    direct = executable_fixture(isolated_windows_discovery / "codex.exe")
    executable_fixture(isolated_windows_discovery / "hash" / "codex.exe")
    assert codex_client.codex_executable() == str(direct)
    monkeypatch.setattr(codex_client.shutil, "which", lambda name: "path-codex.exe")
    assert codex_client.codex_executable() == "path-codex.exe"


def test_explicit_executable_with_spaces_takes_precedence(
    isolated_windows_discovery, monkeypatch, tmp_path
):
    explicit = executable_fixture(tmp_path / "My Custom Codex" / "codex.exe")
    monkeypatch.setenv("JOB_RADAR_CODEX_EXECUTABLE", f'"{explicit}"')
    monkeypatch.setattr(codex_client.shutil, "which", lambda name: "path-codex.exe")
    assert codex_client.codex_executable() == str(explicit)


@pytest.mark.parametrize("override", ["relative/codex.exe", "MISSING_ABSOLUTE"])
def test_invalid_override_is_reported_without_silent_fallback(
    isolated_windows_discovery, monkeypatch, tmp_path, override
):
    value = str(tmp_path / "missing.exe") if override == "MISSING_ABSOLUTE" else override
    monkeypatch.setenv("JOB_RADAR_CODEX_EXECUTABLE", value)
    monkeypatch.setattr(codex_client.shutil, "which", lambda name: "path-codex.exe")
    with pytest.raises(codex_client.MissingCodexCli, match="full file path"):
        codex_client.codex_executable()
    status = codex_client.codex_status()
    assert status["installed"] is False
    assert status["authenticated"] is False
    assert "JOB_RADAR_CODEX_EXECUTABLE" in status["detail"]


def test_discovery_supports_npm_fallback(isolated_windows_discovery, tmp_path):
    npm_cli = executable_fixture(tmp_path / "Roaming Data" / "npm" / "codex.cmd")
    assert codex_client.codex_executable() == str(npm_cli)


def test_discovery_is_bounded_and_missing_message_is_actionable(
    isolated_windows_discovery, monkeypatch
):
    executable_fixture(isolated_windows_discovery / "too" / "deep" / "codex.exe")
    monkeypatch.setattr(
        codex_client.subprocess, "run", lambda *args, **kwargs: pytest.fail("No helper found")
    )
    assert codex_client.codex_executable() is None
    status = codex_client.codex_status()
    assert status["installed"] is False
    assert status["authenticated"] is False
    assert "Check Codex connection" in status["detail"]
    assert "--backend" not in status["detail"]


@pytest.mark.parametrize(
    "login_output, expected",
    [
        ("WARNING noisy path\nLogged in using ChatGPT\n", "Signed in with ChatGPT"),
        ("Logged in using an API key - sk-do-not-display", "Signed in with an API key"),
    ],
)
def test_status_checks_sign_in_without_model_request_or_raw_logs(
    monkeypatch, login_output, expected
):
    monkeypatch.setattr(codex_client, "codex_executable", lambda: "C:/Path With Spaces/codex.exe")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 20
        assert "input" not in kwargs
        output = "WARNING noisy\ncodex-cli 0.158.0-alpha.2.1\n" if command[-1] == "--version" else login_output
        return SimpleNamespace(returncode=0, stdout="", stderr=output)

    monkeypatch.setattr(codex_client.subprocess, "run", fake_run)
    status = codex_client.codex_status()
    assert status["installed"] is True
    assert status["authenticated"] is True
    assert status["version"] == "codex-cli 0.158.0-alpha.2.1"
    assert status["executable"] == "C:/Path With Spaces/codex.exe"
    assert expected in status["detail"]
    assert "WARNING" not in str(status)
    assert "sk-do-not-display" not in str(status)
    assert commands == [
        ["C:/Path With Spaces/codex.exe", "--version"],
        ["C:/Path With Spaces/codex.exe", "login", "status"],
    ]


@pytest.mark.parametrize("failure", ["timeout", "permission", "bad_version", "not_signed_in"])
def test_status_handles_connection_failures(monkeypatch, failure):
    monkeypatch.setattr(codex_client, "codex_executable", lambda: "codex.exe")

    def fake_run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 20)
        if failure == "permission":
            raise PermissionError("private diagnostic text")
        if command[-1] == "--version":
            return SimpleNamespace(returncode=1 if failure == "bad_version" else 0, stdout="codex-cli 1.2.3", stderr="")
        return SimpleNamespace(returncode=1, stdout="private diagnostic text", stderr="Not logged in")

    monkeypatch.setattr(codex_client.subprocess, "run", fake_run)
    status = codex_client.codex_status()
    assert status["authenticated"] is False
    assert status["installed"] is (failure == "not_signed_in")
    assert "Check Codex connection" in status["detail"]
    assert "private diagnostic text" not in str(status)


def test_login_timeout_preserves_known_installation(monkeypatch):
    monkeypatch.setattr(codex_client, "codex_executable", lambda: "codex.exe")

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 1.2.3", stderr="")
        raise subprocess.TimeoutExpired(command, 20)

    monkeypatch.setattr(codex_client.subprocess, "run", fake_run)
    status = codex_client.codex_status()
    assert status["installed"] is True
    assert status["authenticated"] is False
    assert "too long" in status["detail"]
