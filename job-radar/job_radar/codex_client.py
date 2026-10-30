from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .cancellation import check_cancelled, run_process

T = TypeVar("T", bound=BaseModel)


class MissingCodexCli(RuntimeError):
    pass


class CodexExecutionError(RuntimeError):
    pass


def codex_executable() -> str | None:
    """Find the CLI even when Explorer has an older, minimal PATH."""
    override = os.getenv("JOB_RADAR_CODEX_EXECUTABLE", "").strip()
    if override:
        if override.startswith('"') and override.endswith('"'):
            override = override[1:-1]
        path = Path(override)
        if not path.is_absolute() or not path.is_file():
            raise MissingCodexCli(
                "JOB_RADAR_CODEX_EXECUTABLE must point to an existing Codex "
                "executable using its full file path. Correct or remove that "
                "setting, then click Check Codex connection."
            )
        return str(path)

    on_path = shutil.which("codex")
    if on_path:
        return on_path
    if sys.platform != "win32":
        return None

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data and Path(local_app_data).is_absolute():
        binary_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        direct = binary_dir / "codex.exe"
        if direct.is_file():
            return str(direct)
        # Codex desktop installs its helper in a version-specific subdirectory.
        # Search that one level only; never scan unrelated user folders.
        try:
            candidates = [
                (path.stat().st_mtime_ns, str(path))
                for path in binary_dir.glob("*/codex.exe")
                if path.is_file()
            ]
        except OSError:
            candidates = []
        if candidates:
            return max(candidates)[1]

    roaming_app_data = os.getenv("APPDATA")
    if roaming_app_data and Path(roaming_app_data).is_absolute():
        npm_cli = Path(roaming_app_data) / "npm" / "codex.cmd"
        if npm_cli.is_file():
            return str(npm_cli)
    return None


def _missing_cli_message() -> str:
    return (
        "Job Radar could not find the Codex command-line helper. Open or "
        "install the Codex app, then click Check Codex connection. "
        "If Codex is installed elsewhere, set JOB_RADAR_CODEX_EXECUTABLE "
        "to its full executable path."
    )


def _status_process(executable: str, *arguments: str):
    return run_process(
        [executable, *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def codex_status() -> dict[str, str | bool | int]:
    check_cancelled()
    try:
        executable = codex_executable()
    except (MissingCodexCli, OSError) as exc:
        return {
            "installed": False,
            "authenticated": False,
            "detail": str(exc) if isinstance(exc, MissingCodexCli) else _missing_cli_message(),
        }
    if executable is None:
        return {
            "installed": False,
            "authenticated": False,
            "detail": _missing_cli_message(),
        }
    status: dict[str, str | bool | int] = {
        "installed": False,
        "authenticated": False,
        "executable": executable,
    }
    try:
        version = _status_process(executable, "--version")
        if version.returncode != 0:
            status["detail"] = (
                "Codex was found, but its version check failed. Restart Codex "
                "and click Check Codex connection."
            )
            return status
        status["installed"] = True
        # Do not copy warning logs or arbitrary diagnostic output into the GUI.
        version_match = re.search(
            r"\bcodex(?:-cli)?\s+[0-9][A-Za-z0-9.+_-]*",
            version.stdout + "\n" + version.stderr,
        )
        status["version"] = version_match.group(0) if version_match else "Version unavailable"
        login = _status_process(executable, "login", "status")
        status["login_returncode"] = login.returncode
        status["authenticated"] = login.returncode == 0
        if login.returncode == 0:
            login_text = (login.stdout + "\n" + login.stderr).casefold()
            if "chatgpt" in login_text:
                status["detail"] = "Signed in with ChatGPT. No model request was made."
            elif "api key" in login_text:
                status["detail"] = "Signed in with an API key. No model request was made."
            else:
                status["detail"] = "Codex sign-in is available. No model request was made."
        else:
            status["detail"] = (
                "Codex was found, but sign-in is needed. Run codex login to "
                "sign in, then click Check Codex connection."
            )
    except subprocess.TimeoutExpired:
        status["detail"] = (
            "Codex took too long to answer the connection check. Restart "
            "Codex and click Check Codex connection."
        )
    except (OSError, subprocess.SubprocessError):
        status["detail"] = (
            "Job Radar found Codex but could not run its connection check. "
            "Restart Codex and click Check Codex connection."
        )
    return status


def _schema(model_type: type[BaseModel]) -> dict:
    return model_type.model_json_schema()


def _combined_prompt(system: str, user: str) -> str:
    return f"""SYSTEM INSTRUCTIONS
{system}

USER TASK AND SOURCE MATERIAL
{user}

Return only the JSON object required by the supplied output schema.
"""


def structured_response(
    *,
    schema_type: type[T],
    schema_name: str,
    system: str,
    user: str,
    use_web_search: bool,
    reasoning_effort: str = "medium",
    artifact_dir: Path | None = None,
    model: str | None = None,
    use_environment: bool = True,
) -> tuple[T, dict]:
    check_cancelled()
    executable = codex_executable()
    if executable is None:
        raise MissingCodexCli(_missing_cli_message())

    combined_prompt = _combined_prompt(system, user)
    requested_effort = (os.getenv("JOB_RADAR_CODEX_REASONING", reasoning_effort)
                        if use_environment else reasoning_effort).strip()
    requested_model = model if model is not None else (os.getenv("JOB_RADAR_CODEX_MODEL", "").strip() if use_environment else "")
    timeout = int(os.getenv("JOB_RADAR_CODEX_TIMEOUT_SECONDS", "900"))

    def invoke(directory: Path) -> tuple[T, dict]:
        check_cancelled()
        directory.mkdir(parents=True, exist_ok=True)
        schema_path = directory / "codex_output_schema.json"
        output_path = directory / "codex_output.json"
        prompt_path = directory / "codex_prompt.md"
        stderr_path = directory / "codex_stderr.log"
        schema_path.write_text(
            json.dumps(_schema(schema_type), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        prompt_path.write_text(combined_prompt, encoding="utf-8")

        command = [
            executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--color",
            "never",
            "--output-schema",
            str(schema_path.resolve()),
            "--output-last-message",
            str(output_path.resolve()),
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
        ]
        if requested_model:
            command.extend(["--model", requested_model])
        if requested_effort:
            command.extend(
                ["-c", f"model_reasoning_effort={json.dumps(requested_effort)}"]
            )
        if use_web_search:
            command.extend(["-c", 'web_search="live"'])
        command.append("-")

        process = run_process(
            command,
            cwd=directory,
            input=combined_prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        check_cancelled()
        stderr_path.write_text(process.stderr, encoding="utf-8")
        if process.returncode != 0:
            hint = ""
            lowered = (process.stdout + process.stderr).casefold()
            if "login" in lowered or "auth" in lowered or "unauthorized" in lowered:
                hint = " Run `codex login` and sign in with ChatGPT, then retry."
            raise CodexExecutionError(
                f"Codex exited with status {process.returncode}.{hint} "
                f"See {stderr_path}."
            )
        if not output_path.exists():
            raise CodexExecutionError(
                "Codex completed without writing its structured output. "
                f"See {stderr_path}."
            )

        output_text = output_path.read_text(encoding="utf-8")
        parsed = schema_type.model_validate_json(output_text)
        check_cancelled()
        raw = {
            "backend": "codex",
            "schema_name": schema_name,
            "model_override": requested_model or None,
            "reasoning_effort": requested_effort or None,
            "returncode": process.returncode,
            "stdout_tail": process.stdout[-4_000:],
            "stderr_log": stderr_path.name,
        }
        return parsed, raw

    if artifact_dir is not None:
        return invoke(artifact_dir)
    with tempfile.TemporaryDirectory(prefix="job-radar-codex-") as temp_dir:
        return invoke(Path(temp_dir))


def codex_request_preview(
    system: str,
    user: str,
    schema_type: type[BaseModel],
) -> str:
    return json.dumps(
        {
            "backend": "codex",
            "model": os.getenv("JOB_RADAR_CODEX_MODEL") or "Codex CLI default",
            "reasoning_effort": os.getenv(
                "JOB_RADAR_CODEX_REASONING", "medium"
            ),
            "sandbox": "read-only",
            "web_search": "disabled",
            "schema": schema_type.model_json_schema(),
            "system": system,
            "user": user,
        },
        ensure_ascii=False,
        indent=2,
    )
