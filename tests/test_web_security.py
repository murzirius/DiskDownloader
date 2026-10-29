import io
import json
from pathlib import Path
import types

import pytest

from diskdownloader.auth import Auth, client_config
from diskdownloader.errors import AppError
from diskdownloader.quota import Quota, DEFAULTS
from diskdownloader.store import Store
from diskdownloader.web import create_app


@pytest.fixture
def web(tmp_path):
    store=Store(tmp_path)
    auth=types.SimpleNamespace(project="test-project",public=lambda:{"connected":False})
    engine=types.SimpleNamespace(active=None,busy=lambda:False,live={})
    drive=types.SimpleNamespace(quota=Quota(store))
    app=create_app(store,auth,drive,engine)
    app.config.update(TESTING=True)
    client=app.test_client()
    client.environ_base.update(REMOTE_ADDR="127.0.0.1")
    return store,app,client


def test_language_follows_browser_and_explicit_selection(web):
    store,app,client=web
    assert b'<html lang="ru">' in client.get("/",base_url="http://127.0.0.1:8767",headers={"Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8"}).data
    assert b'<html lang="en">' in client.get("/",base_url="http://127.0.0.1:8767",headers={"Accept-Language":"en-US"}).data
    store.set("language","ru")
    assert b'<html lang="ru">' in client.get("/",base_url="http://127.0.0.1:8767",headers={"Accept-Language":"en"}).data


@pytest.mark.parametrize("host",["evil.example","localhost:8767","127.0.0.1.evil.example"])
def test_dns_rebinding_hosts_are_rejected(web,host):
    _,app,client=web
    assert client.get("/api/state",base_url="http://"+host).status_code==403


def test_mutations_require_csrf_and_same_origin(web):
    _,app,client=web
    url="http://127.0.0.1:8767"
    assert client.post("/api/settings",base_url=url,json={"language":"ru"}).status_code==403
    headers={"X-App-Token":app.extensions["csrf"],"Origin":"https://evil.example"}
    assert client.post("/api/settings",base_url=url,json={"language":"ru"},headers=headers).status_code==403
    headers["Origin"]=url
    assert client.post("/api/settings",base_url=url,json={"language":"ru"},headers=headers).status_code==200


def test_local_quotas_are_explicitly_non_authoritative(web):
    store,app,client=web
    quota=Quota(store,clock=lambda:1000000)
    quota.record("test-project",units=100,requests=1,bytes_count=1024,limited=1)
    value=quota.snapshot("test-project")
    assert value["day"]==dict(requests=1,units=100,bytes=1024,limited=1)
    assert value["authoritative"] is False and value["source"]=="local"
    assert Quota(store,clock=lambda:1000000+86401).snapshot("test-project")["day"]["units"]==0


def test_quota_project_isolation_and_persistence(web):
    store,_,_=web
    quota=Quota(store)
    quota.record("first",units=10,requests=2)
    quota.record("second",units=20,requests=1)
    assert Quota(Store(store.path.parent)).snapshot("first")["day"]["units"]==10
    assert quota.snapshot("second")["day"]["units"]==20


def test_reference_values_do_not_change_google_quotas(web):
    store,app,client=web
    limits={**DEFAULTS,"user_minute":1234}
    response=client.post("/api/settings",base_url="http://127.0.0.1:8767",json={"limits":limits},headers={"X-App-Token":app.extensions["csrf"]})
    assert response.status_code==200
    assert Quota(store).snapshot("test-project")["limits"]["user_minute"]==1234


def oauth_config():
    return {"installed":{"client_id":"synthetic.apps.googleusercontent.com","project_id":"test-project","client_secret":"synthetic-not-a-real-secret","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}}


@pytest.mark.parametrize("field,value",[("token_uri","https://evil.example/token"),("auth_uri","https://evil.example/auth"),("client_id","invalid"),("project_id","../../private")])
def test_uploaded_oauth_json_cannot_redirect_credentials(field,value):
    config=oauth_config();config["installed"][field]=value
    with pytest.raises(AppError):client_config(config)


def test_service_account_keys_are_not_accepted():
    with pytest.raises(AppError):client_config({"type":"service_account","private_key":"not-a-real-key"})


def test_credentials_never_fall_back_to_plaintext(tmp_path,monkeypatch):
    store=Store(tmp_path)
    def failed(*args):raise RuntimeError("locked")
    monkeypatch.setattr("diskdownloader.auth.keyring.set_password",failed)
    auth=Auth(store);auth.configure(oauth_config())
    assert not auth.persistent
    assert store.get("profile") is None
    assert not list(tmp_path.glob("*.json"))
    assert b"synthetic-not-a-real-secret" not in store.path.read_bytes()


def test_error_responses_do_not_expose_exception_details(web):
    _,app,client=web
    @app.get("/failure")
    def failure():raise RuntimeError("private-token-and-path")
    response=client.get("/failure",base_url="http://127.0.0.1:8767")
    assert response.status_code==500
    assert b"private-token" not in response.data
    assert response.json["error"]=="application_failed"


def test_translations_cover_all_interface_and_error_keys():
    import ast,re
    package=Path(__file__).resolve().parents[1]/"diskdownloader"
    en=json.loads((package/"locales/en.json").read_text(encoding="utf-8"))
    ru=json.loads((package/"locales/ru.json").read_text(encoding="utf-8"))
    assert en.keys()==ru.keys()
    keys=set(re.findall(r"text\.([a-z_0-9]+)",(package/"templates/index.html").read_text(encoding="utf-8")))
    keys.update(re.findall(r"\bT\.([a-z_]+)",(package/"static/app.js").read_text(encoding="utf-8")))
    for source in package.glob("*.py"):
        tree=ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="AppError" and node.args and isinstance(node.args[0],ast.Constant):keys.add(node.args[0].value)
    assert not keys-en.keys()
