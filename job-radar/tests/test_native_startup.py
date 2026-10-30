"""Exercise the actual Tk root once per process, as the desktop launcher does."""
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
import job_radar


@pytest.mark.parametrize('launch', range(3))
def test_native_root_saves_and_closes_in_fresh_process(tmp_path, launch):
    script = textwrap.dedent('''
        import socket, sys, time, tkinter as tk
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        def denied(*args, **kwargs):
            raise AssertionError('Native startup check attempted a network call')
        socket.socket.connect = denied
        socket.socket.connect_ex = denied
        socket.getaddrinfo = denied
        from job_radar.gui import JobRadarApp
        from job_radar.gui_services import GuiService
        root = tk.Tk()
        root.withdraw()
        app = JobRadarApp(root, GuiService(Path(sys.argv[2])))
        errors = []
        app._error = lambda error: errors.append(str(error))
        app._fill_draft({'company':'Fictional Lab', 'title':'Research Coordinator',
            'location':'Test City', 'source_url':'https://example.invalid/test',
            'description':'Coordinate laboratory experiments.'})
        app.save_job()
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not app.busy, 'Save did not finish'
        assert not errors, errors
        assert app._worker is None
        assert app.service.jobs()[0]['company'] == 'Fictional Lab'
        app._close()
        assert app._closed and app._poll_timer is None
        print('Native save and close completed')
    ''')
    result = subprocess.run([sys.executable, '-c', script,
                             str(Path(job_radar.__file__).resolve().parents[1]), str(tmp_path)],
                            capture_output=True, text=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Native save and close completed' in result.stdout
