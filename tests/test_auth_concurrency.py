import threading
import time
import types

from diskdownloader.auth import Auth
from diskdownloader.store import Store


def test_status_does_not_wait_for_google_token_refresh(tmp_path):
    auth=Auth(Store(tmp_path))
    entered=threading.Event();release=threading.Event()
    class Credential:
        valid=False
        token="synthetic-not-real"
        def refresh(self,request):
            entered.set();assert release.wait(5)
            self.valid=True
        def to_json(self):return '{}'
    auth.credentials=Credential()
    auth._save=lambda:None
    result=[]
    worker=threading.Thread(target=lambda:result.append(auth.token()))
    worker.start();assert entered.wait(2)
    started=time.monotonic()
    assert auth.public()["connected"]
    assert time.monotonic()-started<.2
    release.set();worker.join(5)
    assert result==["synthetic-not-real"]


def test_parallel_downloads_refresh_once(tmp_path):
    auth=Auth(Store(tmp_path));calls=[]
    class Credential:
        valid=False
        token="synthetic-not-real"
        def refresh(self,request):
            calls.append(1);time.sleep(.02);self.valid=True
    auth.credentials=Credential();auth._save=lambda:None
    workers=[threading.Thread(target=auth.token) for _ in range(8)]
    for worker in workers:worker.start()
    for worker in workers:worker.join(5)
    assert calls==[1]


def test_invalid_access_token_has_a_visible_reconnect_reason(tmp_path):
    auth=Auth(Store(tmp_path))
    auth.credentials=types.SimpleNamespace(token='synthetic-not-real')
    auth.invalidate()
    assert auth.public()['error']=='google_signin_expired'
    assert auth.credentials.token is None
