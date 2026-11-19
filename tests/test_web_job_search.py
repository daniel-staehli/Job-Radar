import httpx
import pytest
from job_radar.web_job_search import find_links
from test_gui_integrations import app, finish


def test_only_search_terms_leave_app_and_results_are_deduplicated(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "example-only")
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={"web": {"results": [
            {"url": "https://example.org/jobs/1", "title": "Research &amp; Development"},
            {"url": "https://example.org/jobs/1", "title": "Duplicate"},
            {"url": "javascript:alert()", "title": "Invalid"}]}})
    assert find_links("Scientist", "Basel", transport=httpx.MockTransport(reply)) == [
        ("Research & Development", "https://example.org/jobs/1")]
    assert len(calls) == 1
    assert calls[0].url.host == "api.search.brave.com"
    assert calls[0].url.params["q"] == "Scientist Basel jobs vacancies careers"
    assert calls[0].headers["X-Subscription-Token"] == "example-only"


def test_no_key_no_request(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    with pytest.raises(ValueError, match="BRAVE_SEARCH_API_KEY"):
        find_links("Scientist", "", transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request")))


def test_result_can_be_fetched_in_import_form(app, monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from job_radar import web_job_search
    url = "https://example.org/jobs/test"
    monkeypatch.setattr(web_job_search, "find_links", lambda role, where: [("Example role", url)])
    selected = []
    monkeypatch.setattr(app, "fetch_job", lambda: selected.append(app.import_url.get()))
    app.search_web_jobs()
    dialog = next(w for w in app.window.winfo_children() if isinstance(w, tk.Toplevel)
                  and w.title() == "Search jobs across employers")
    frame, = dialog.winfo_children()
    controls = frame.winfo_children()
    next(w for w in controls if isinstance(w, ttk.Button) and "Brave" in w["text"]).invoke()
    finish(app)
    tree = next(w for w in controls if isinstance(w, ttk.Treeview))
    tree.selection_set(tree.get_children()[0])
    next(w for w in controls if isinstance(w, ttk.Button) and "Fetch" in w["text"]).invoke()
    assert selected == [url]
    assert app.tabs.select() == str(app.import_tab)
