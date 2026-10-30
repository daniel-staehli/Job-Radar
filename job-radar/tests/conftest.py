"""Keep tests independent of the developer's AI settings and network access."""
import json
import os
import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_ai_environment(tmp_path, monkeypatch):
    # Metadata lives outside application fixture roots so read-only tests can
    # still assert that opening the app does not create its workspace files.
    home = tmp_path.parent / (tmp_path.name + "-codex-home")
    monkeypatch.setenv("CODEX_HOME", str(home))
    for name in tuple(os.environ):
        if name.startswith("JOB_RADAR_") or name in {
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        }:
            monkeypatch.delenv(name, raising=False)

    def deny_network(*args, **kwargs):
        raise AssertionError("Tests must use synthetic responses, never a live network")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    return home


@pytest.fixture
def synthetic_codex_metadata(isolated_ai_environment):
    """Explicit local metadata for tests which exercise a configured provider."""
    home = isolated_ai_environment
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'model = "synthetic-test-model"\nmodel_reasoning_effort = "medium"\n',
        encoding="utf-8",
    )
    (home / "models_cache.json").write_text(json.dumps({"models": [{
        "slug": "synthetic-test-model",
        "supported_reasoning_levels": ["low", "medium", "high"],
        "default_reasoning_level": "medium",
    }]}), encoding="utf-8")
    return home


@pytest.fixture(scope="session")
def tk_session_root():
    """One Tcl/Tk interpreter per test process; each case owns a fresh window.

    Repeated Tk interpreter teardown/recreation coincided with intermittent
    Windows invalid-handle crashes in the combined suite. Keep one runtime,
    matching the desktop process and avoiding shared native event-queue state. Native entry-point
    tests separately exercise a real Tk root in a fresh process.
    """
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()
