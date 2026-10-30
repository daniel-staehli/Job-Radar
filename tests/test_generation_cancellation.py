"""Cancel synthetic processes and mock HTTP requests, never real model calls."""
import asyncio
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx
import pytest
from pydantic import BaseModel

from job_radar import cancellation, codex_client, providers
from job_radar.cancellation import (CancellationToken, GenerationCancelled,
    cancellation_scope, check_cancelled, current_token, run_process)


@pytest.fixture(autouse=True)
def mock_http_event_loop_without_network_sockets(monkeypatch):
    # Provider traffic is MockTransport. Use a timer-only event loop so the
    # Windows stdlib wake-up socket cannot conflict with the strict network
    # guard; any unexpected file/socket registration still fails the test.
    import selectors
    class TimersOnlySelector(selectors.SelectSelector):
        def select(self, timeout=None):
            assert not self.get_map(), "Mock HTTP tests attempted real I/O"
            if timeout:
                time.sleep(timeout)
            return []
    class MockHttpLoop(asyncio.SelectorEventLoop):
        def __init__(self):
            super().__init__(TimersOnlySelector())
        def _make_self_pipe(self):
            pass
        def _write_to_self(self):
            pass
        def _close_self_pipe(self):
            pass
    class Policy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            return MockHttpLoop()
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(Policy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


def _worker(token, action):
    errors, results = [], []
    def run():
        try:
            with cancellation_scope(token):
                results.append(action())
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors, results


def test_token_is_thread_local_and_nested_scope_restores_parent(tmp_path):
    outer, inner = CancellationToken(), CancellationToken()
    outer.track_output(tmp_path / 'new')
    outer.track_output(tmp_path / 'new')
    assert outer.outputs == (tmp_path / 'new',)
    assert current_token() is None
    with cancellation_scope(outer):
        assert current_token() is outer
        with cancellation_scope(inner):
            inner.cancel()
            inner.cancel()
            with pytest.raises(GenerationCancelled):
                check_cancelled()
        assert current_token() is outer
        seen = []
        thread = threading.Thread(target=lambda: seen.append(current_token()))
        thread.start(); thread.join(1)
        assert seen == [None]
        check_cancelled()
    assert current_token() is None


def test_cancelled_scope_never_enters_body_or_starts_subprocess(monkeypatch):
    token = CancellationToken(); token.cancel()
    monkeypatch.setattr(cancellation.subprocess, 'Popen', lambda *a, **k: pytest.fail('No process should start'))
    with pytest.raises(GenerationCancelled):
        with cancellation_scope(token):
            run_process(['unused'])
    assert current_token() is None


def test_non_cancellable_subprocess_preserves_existing_call_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(cancellation.subprocess, 'run', lambda *a, **kw: calls.append((a, kw)) or 'normal')
    assert run_process(['example'], capture_output=True, timeout=1) == 'normal'
    assert calls == [((['example'],), {'capture_output': True, 'timeout': 1})]


def test_cancellable_process_returns_success_with_unicode_stdin():
    token = CancellationToken()
    with cancellation_scope(token):
        result = run_process([sys.executable, '-c', 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'],
                             input='Grüße à Genève', text=True, encoding='utf-8', capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == 'Grüße à Genève'


def test_aborting_process_kills_its_child_tree_but_not_an_unrelated_process(tmp_path):
    ready, late = tmp_path/'ready', tmp_path/'late'
    child_code = 'import pathlib,time; time.sleep(1.4); pathlib.Path(' + repr(str(late)) + ').write_text("orphan")'
    parent_code = ('import subprocess,sys,pathlib,time; subprocess.Popen([sys.executable,"-c",' + repr(child_code) + ']); '
                   'pathlib.Path(' + repr(str(ready)) + ').write_text("ready"); time.sleep(20)')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'],
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    token = CancellationToken()
    thread, errors, results = _worker(token, lambda: run_process(
        [sys.executable, '-c', parent_code], capture_output=True, text=True, timeout=10))
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        started = time.monotonic()
        token.cancel()
        thread.join(4)
        assert not thread.is_alive()
        assert time.monotonic() - started < 4
        assert len(errors) == 1 and isinstance(errors[0], GenerationCancelled)
        assert results == []
        assert unrelated.poll() is None
        time.sleep(1.5)
        assert not late.exists(), 'A cancelled helper left a child process running'
    finally:
        token.cancel(); thread.join(5)
        unrelated.kill(); unrelated.wait(5)


def test_abort_is_responsive_when_helper_never_reads_large_stdin():
    token = CancellationToken()
    thread, errors, _ = _worker(token, lambda: run_process(
        [sys.executable, '-c', 'import time; time.sleep(20)'],
        input='x' * 1_000_000, text=True, capture_output=True, timeout=10))
    time.sleep(.15)
    token.cancel(); thread.join(4)
    assert not thread.is_alive()
    assert isinstance(errors[0], GenerationCancelled)


def test_cancellable_process_keeps_timeout_and_exit_failure_behavior():
    with cancellation_scope(CancellationToken()):
        with pytest.raises(subprocess.TimeoutExpired):
            run_process([sys.executable, '-c', 'import time; time.sleep(10)'], capture_output=True, timeout=.15)
        with pytest.raises(subprocess.CalledProcessError) as error:
            run_process([sys.executable, '-c', 'import sys; print("failed"); sys.exit(7)'],
                        text=True, capture_output=True, check=True, timeout=5)
    assert error.value.returncode == 7
    assert 'failed' in error.value.stdout


class BlockedStream(httpx.AsyncByteStream):
    def __init__(self, entered, closed):
        self.entered, self.closed = entered, closed
    async def __aiter__(self):
        self.entered.set()
        yield b'{"answer":'
        await asyncio.Event().wait()
    async def aclose(self):
        self.closed.set()


@pytest.mark.parametrize('before_headers', [True, False])
def test_abort_interrupts_http_before_headers_or_during_body_and_closes_request(before_headers):
    token = CancellationToken()
    entered, closed = threading.Event(), threading.Event()
    calls = []
    async def handler(request):
        calls.append(request.url)
        if before_headers:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return httpx.Response(200, stream=BlockedStream(entered, closed))
    transport = httpx.MockTransport(handler)
    thread, errors, results = _worker(token, lambda: providers._post(
        'http://localhost:11434/api/chat', {}, {'model': 'synthetic'}, transport=transport))
    assert entered.wait(3)
    token.cancel(); thread.join(3)
    assert not thread.is_alive()
    assert closed.is_set()
    assert len(calls) == 1 and results == []
    assert len(errors) == 1 and isinstance(errors[0], GenerationCancelled)


def test_cancel_before_http_request_does_not_send_anything():
    token = CancellationToken()
    with cancellation_scope(token):
        token.cancel()
        with pytest.raises(GenerationCancelled):
            providers._post('http://localhost:11434/api/chat', {}, {},
                transport=httpx.MockTransport(lambda request: pytest.fail('Unexpected provider call')))


def test_cancellable_http_success_retains_request_and_json_behavior():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'answer': 'synthetic'})
    with cancellation_scope(CancellationToken()):
        result = providers._post('http://localhost:11434/api/chat', {'X-Example':'test'}, {'model':'chosen'},
                                transport=httpx.MockTransport(handler))
    assert result == {'answer':'synthetic'}
    assert len(calls) == 1 and calls[0].headers['x-example'] == 'test'
    assert calls[0].content == b'{"model": "chosen"}'


@pytest.mark.parametrize('code, message', [(302, 'redirect'), (401, 'authentication'), (429, 'usage or rate'), (500, 'failed')])
def test_cancellable_http_keeps_status_errors_without_retries(code, message):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, headers={'Location':'https://unrelated.example/'}, text='private raw response')
    with cancellation_scope(CancellationToken()), pytest.raises(providers.ProviderError, match=message) as error:
        providers._post('http://localhost:11434/api/chat', {}, {}, transport=httpx.MockTransport(handler))
    assert len(calls) == 1 and 'private raw response' not in str(error.value)


def test_cancellable_http_timeout_closes_stream():
    entered, closed = threading.Event(), threading.Event()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=BlockedStream(entered, closed)))
    with cancellation_scope(CancellationToken()), pytest.raises(providers.ProviderError, match='timed out'):
        providers._post('http://localhost:11434/api/chat', {}, {}, transport=transport, timeout=.1)
    assert entered.is_set() and closed.is_set()


def test_cancellable_http_keeps_response_size_limit(monkeypatch):
    monkeypatch.setattr(providers, 'MAX_RESPONSE_BYTES', 10)
    with cancellation_scope(CancellationToken()), pytest.raises(providers.ProviderError, match='size limit'):
        providers._post('http://localhost:11434/api/chat', {}, {},
                        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x'*11)))


class ExampleOutput(BaseModel):
    answer: str


def test_codex_connection_check_preserves_cancellation(monkeypatch):
    monkeypatch.setattr(codex_client, 'codex_executable', lambda: 'synthetic-cli')
    def abort(*args, **kwargs):
        raise GenerationCancelled('stopped')
    monkeypatch.setattr(codex_client, 'run_process', abort)
    with cancellation_scope(CancellationToken()), pytest.raises(GenerationCancelled):
        codex_client.codex_status()


def test_codex_output_is_not_consumed_if_abort_arrives_during_process_completion(tmp_path, monkeypatch):
    token = CancellationToken()
    monkeypatch.setattr(codex_client, 'codex_executable', lambda: 'synthetic-cli')
    def finish_then_cancel(*args, **kwargs):
        (tmp_path/'codex_output.json').write_text('{"answer":"synthetic"}', encoding='utf-8')
        token.cancel()
        return subprocess.CompletedProcess(args[0], 0, '', '')
    monkeypatch.setattr(codex_client, 'run_process', finish_then_cancel)
    with cancellation_scope(token), pytest.raises(GenerationCancelled):
        codex_client.structured_response(schema_type=ExampleOutput, schema_name='test',
            system='Synthetic', user='Synthetic', use_web_search=False, artifact_dir=tmp_path)
