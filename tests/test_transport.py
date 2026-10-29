import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import types

import pytest
import requests

from diskdownloader.drive import Drive
from diskdownloader.errors import AppError
from diskdownloader.quota import Quota
from diskdownloader.store import Store


@pytest.fixture
def http_drive(tmp_path):
    capture=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            capture.append((self.path,self.headers.get('Authorization'),self.headers.get('Range')))
            code=429 if '/limited' in self.path else 403 if '/denied' in self.path else 200
            body=json.dumps({'error':{'errors':[{'reason':'userRateLimitExceeded' if code==429 else 'forbidden'}]}}).encode() if code!=200 else b'{"files":[]}'
            self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    auth=types.SimpleNamespace(project='test-project',token=lambda:'synthetic-not-a-real-token')
    drive=Drive(auth,Quota(Store(tmp_path)));drive.BASE=f'http://127.0.0.1:{server.server_port}/'
    yield drive,capture
    server.shutdown();server.server_close();thread.join(5)


def test_actual_http_auth_and_request_accounting(http_drive):
    drive,capture=http_drive
    assert drive.children('root')=={'files':[]}
    assert capture[0][1]=='Bearer synthetic-not-a-real-token'
    assert 'pageSize=1000' in capture[0][0]
    assert 'thumbnail' not in capture[0][0]
    assert drive.quota.snapshot('test-project')['day']['units']==100


def test_google_rate_limit_is_retryable_and_counted(http_drive):
    drive,_=http_drive
    with pytest.raises(AppError) as error:drive.request('limited')
    assert error.value.code=='google_quota' and error.value.retryable
    assert drive.quota.snapshot('test-project')['day']['limited']==1


def test_google_permission_error_is_not_retried_as_quota(http_drive):
    drive,_=http_drive
    with pytest.raises(AppError) as error:drive.request('denied')
    assert error.value.code=='google_download_denied' and not error.value.retryable
    assert drive.quota.snapshot('test-project')['day']['limited']==0


def test_redirect_cannot_send_oauth_token_to_third_party(tmp_path):
    auth=types.SimpleNamespace(project='test-project',token=lambda:'synthetic-not-real')
    drive=Drive(auth,Quota(Store(tmp_path)))
    calls=[]
    class Response:
        status_code=302
        url='https://www.googleapis.com/drive/v3/files/test'
        headers={'Location':'https://evil.example/steal'}
        def close(self):pass
    class Session:
        def get(self,*args,**kwargs):calls.append(args[0]);return Response()
    drive.local.session=Session()
    with pytest.raises(AppError):drive.content('test',0)
    assert len(calls)==1


def test_signed_content_redirect_does_not_receive_bearer_token(tmp_path):
    auth=types.SimpleNamespace(project='test-project',token=lambda:'synthetic-not-real')
    drive=Drive(auth,Quota(Store(tmp_path)))
    calls=[]
    class Response:
        status_code=302
        url='https://www.googleapis.com/drive/v3/files/test'
        headers={'Location':'https://download.googleusercontent.com/original?signature=synthetic'}
        def close(self):pass
    class Session:
        def get(self,url,**kwargs):
            calls.append((url,dict(kwargs['headers'])))
            return Response() if len(calls)==1 else types.SimpleNamespace(status_code=200)
    drive.local.session=Session()
    drive.content('test',0)
    assert 'Authorization' in calls[0][1]
    assert 'Authorization' not in calls[1][1]
