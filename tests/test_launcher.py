import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import requests


def test_shutdown_finishes_http_workers_before_closing_sockets(tmp_path):
    # Run the real launcher in a child so its signal handlers and service threads
    # cannot interfere with pytest. Observe the Waitress trigger on every OS,
    # including Windows where closing it during select() does not raise EBADF.
    script = '''
import threading
import waitress
from diskdownloader.launcher import main

create_server = waitress.create_server
closing = []
servers = []
shutdown_requested = threading.Event()

def observe(application, **kwargs):
    def app(environ, start_response):
        response = application(environ, start_response)
        if environ["PATH_INFO"] == "/api/shutdown":
            assert shutdown_requested.wait(10), "shutdown did not start"
        return response

    server = create_server(app, **kwargs)
    servers.append(server)
    owner = threading.get_ident()
    close = server.trigger.close
    shutdown = server.task_dispatcher.shutdown

    def record_close():
        closing.append((threading.get_ident() == owner,
                        not server.task_dispatcher.threads))
        return close()

    def record_shutdown(*args, **kwargs):
        shutdown_requested.set()
        return shutdown(*args, **kwargs)

    server.trigger.close = record_close
    server.task_dispatcher.shutdown = record_shutdown
    return server

waitress.create_server = observe
assert main(["--headless", "--no-browser"]) == 0
assert closing and all(owner and drained for owner, drained in closing), closing
assert not servers[0]._map, "server left sockets open"
assert not servers[0].task_dispatcher.threads, "HTTP workers did not stop"
assert not any(t.name == "local-service" for t in threading.enumerate())
'''
    project = Path(__file__).resolve().parents[1]
    state = tmp_path / "state"
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script], cwd=project,
        env={**os.environ, "DISKDOWNLOADER_STATE": str(state), "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        runtime = state / "runtime.json"
        deadline = time.monotonic() + 20
        while not runtime.exists():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "service did not start"
            time.sleep(.05)
        info = json.loads(runtime.read_text(encoding="utf-8"))
        url = info["url"]
        with requests.Session() as session:
            session.trust_env = False
            assert session.get(url, timeout=5).status_code == 403
            page = session.get(url, params={"key": info["key"]}, timeout=5)
            token = re.search(r'name="app-token" content="([^"]+)"', page.text).group(1)
            response = session.post(url + "api/shutdown", headers={"X-App-Token": token},
                                    json={}, timeout=15)
            assert response.status_code == 200
        output, errors = process.communicate(timeout=20)
        assert process.returncode == 0, output + errors
        assert not errors, errors
        assert not runtime.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
