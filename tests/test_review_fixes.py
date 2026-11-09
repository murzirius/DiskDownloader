import os
import types

import pytest

from diskdownloader import paths
from diskdownloader.drive import Drive
from diskdownloader.engine import Engine
from diskdownloader.errors import AppError
from diskdownloader.paths import component, publish, safe_path
from diskdownloader.quota import Quota
from diskdownloader.store import Store


@pytest.mark.parametrize("name", ["word " * 40, "dot." * 60, "a" * 169 + " b" * 20, "я " * 100,
                                  "file." + "a" * 19 + " " + "b" * 10, "x." + " " * 30])
def test_truncated_names_never_end_with_space_or_dot(tmp_path, name):
    result = component(name)
    assert result and not result.endswith((" ", "."))
    safe_path(tmp_path, result)


@pytest.mark.skipif(os.name == "nt", reason="POSIX publish path")
def test_publish_falls_back_to_rename_without_hard_links(tmp_path, monkeypatch):
    def unsupported(*args):
        raise PermissionError("hard links are not supported")
    monkeypatch.setattr(paths.os, "link", unsupported)
    part, target = tmp_path / "x.part", tmp_path / "x"
    part.write_bytes(b"data")
    publish(part, target)
    assert target.read_bytes() == b"data" and not part.exists()
    part.write_bytes(b"other")
    with pytest.raises(FileExistsError):
        publish(part, target)
    assert target.read_bytes() == b"data" and part.exists()


def make_drive(responses):
    class Response:
        def __init__(self, status, body):
            self.status_code, self.body, self.headers = status, body, {}
            self.url = "https://www.googleapis.com/drive/v3/files"
        def json(self):
            return self.body
        def close(self):
            pass
    auth = types.SimpleNamespace(project="p", token=lambda: "t", invalidate=lambda: None)
    drive = Drive(auth, types.SimpleNamespace(record=lambda *a, **k: None))
    drive.local.session = types.SimpleNamespace(get=lambda *a, **k: Response(*responses))
    return drive


@pytest.mark.parametrize("body,code", [
    ({"error": {"errors": [{"reason": "accessNotConfigured"}]}}, "drive_api_disabled"),
    ({"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}, "drive_api_disabled"),
    ({"error": {"errors": [{"reason": "insufficientPermissions"}]}}, "drive_permission_required"),
    ({"error": {"errors": [{"reason": "forbidden"}]}}, "google_download_denied"),
    ({}, "google_download_denied"),
])
def test_forbidden_responses_get_specific_messages(body, code):
    with pytest.raises(AppError) as error:
        make_drive((403, body)).account()
    assert error.value.code == code


def test_overview_cache_follows_job_state(tmp_path):
    store = Store(tmp_path)
    identity = store.create_job("p", "", "root", "Archive", str(tmp_path), 1)
    assert store.overview()[0]["total"] == 0
    with store.db() as db:
        db.execute("INSERT INTO files(job,id,local,folded,metadata,status) VALUES(?,?,?,?,?,?)", (identity, "f", "f", "f", "{}", "complete"))
    store.change_job(identity, status="complete")
    assert store.overview()[0]["complete"] == 1
    store.delete_job(identity)
    assert store.overview() == []


def test_engine_refuses_new_work_after_shutdown_begins(tmp_path):
    store = Store(tmp_path)
    engine = Engine(store, types.SimpleNamespace(auth=types.SimpleNamespace(project="p")))
    identity = store.create_job("p", "", "root", "Archive", str(tmp_path), 1)
    engine.close()
    with pytest.raises(AppError):
        engine.start(identity)
    assert not engine.busy()
