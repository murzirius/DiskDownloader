import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import requests


def test_detached_service_starts_outside_source_directory_and_stops(tmp_path):
    project=Path(__file__).resolve().parents[1]
    state=tmp_path/'private-state'
    result=subprocess.run([sys.executable,'-B',str(project/'run.py'),'--background','--no-browser'],cwd=tmp_path,
        env={**os.environ,'DISKDOWNLOADER_STATE':str(state),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=15)
    assert result.returncode==0
    runtime=state/'runtime.json'
    deadline=time.monotonic()+20
    while not runtime.exists():
        assert time.monotonic()<deadline,'background service did not start'
        time.sleep(.1)
    url=json.loads(runtime.read_text(encoding='utf-8'))['url']
    session=requests.Session();session.trust_env=False
    try:
        page=session.get(url,timeout=5)
        assert page.status_code==200
        token=re.search(r'name="app-token" content="([^"]+)"',page.text).group(1)
        assert session.get(url+'api/state',timeout=5).json()['auth']['connected'] is False
    finally:
        session.post(url+'api/shutdown',headers={'X-App-Token':token},json={},timeout=10)
        deadline=time.monotonic()+15
        while runtime.exists():
            assert time.monotonic()<deadline,'background service did not stop'
            time.sleep(.1)
