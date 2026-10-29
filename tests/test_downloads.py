import hashlib
import io
import json
from pathlib import Path
import threading
import types

import pytest
import requests

from diskdownloader.drive import FOLDER
from diskdownloader.engine import Engine, fingerprint
from diskdownloader.errors import AppError, Interrupted
from diskdownloader.quota import Quota
from diskdownloader.store import Store


def metadata(identity, name, content=b"", mime="image/jpeg", version="1"):
    return dict(id=identity, name=name, size=str(len(content)), mimeType=mime,
                version=version, md5Checksum=hashlib.md5(content).hexdigest(),
                sha256Checksum=hashlib.sha256(content).hexdigest(), modifiedTime="2026-01-02T03:04:05Z", capabilities={"canDownload": True})


class Auth:
    project = "test-project"
    def public(self):
        return {"project": self.project, "connected": True, "configured": True, "pending": False, "persistent": False, "error": ""}


class Content:
    def __init__(self, content, offset=0, ignored=False, wrong_range=False, fail_after=None):
        self.content = content
        self.offset = 0 if ignored else offset
        self.status_code = 200 if not offset or ignored else 206
        self.headers = {"Content-Range": f"bytes {offset+int(wrong_range)}-{len(content)-1}/{len(content)}"}
        self.fail_after = fail_after
        self.closed = False
    def iter_content(self, size):
        for start in range(self.offset, len(self.content), size):
            if self.fail_after is not None and start >= self.fail_after:
                raise requests.ConnectionError()
            yield self.content[start:start+size]
    def close(self):
        self.closed = True


class Drive:
    def __init__(self, store, objects, folders):
        self.auth = Auth()
        self.quota = Quota(store)
        self.objects, self.folders = objects, folders
        self.downloads, self.listings = [], []
        self.ignored = self.wrong_range = False
        self.fail_after = None
        self.account_id = "account-a"
    def account(self):
        return {"user": {"permissionId": self.account_id}}
    def get(self, identity):
        if identity == "root":
            identity = "folder-root"
        return dict(self.objects[identity][0])
    def children(self, identity, page=""):
        self.listings.append((identity,page))
        return self.folders[(identity,page)]
    def content(self, identity, offset):
        self.downloads.append((identity,offset))
        return Content(self.objects[identity][1], offset, self.ignored, self.wrong_range, self.fail_after)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    store = Store(state)
    monkeypatch.setattr("diskdownloader.engine.shutil.disk_usage", lambda p: types.SimpleNamespace(free=10**12))
    return store, tmp_path / "archive"


def setup_job(store, root, blobs, workers=2):
    folder = metadata("folder-root", "Archive", mime=FOLDER)
    objects = {"folder-root": (folder,b"")}
    for identity,name,content in blobs:
        objects[identity] = (metadata(identity,name,content),content)
    drive = Drive(store, objects, {("folder-root",""): {"files": [m for k,(m,c) in objects.items() if k!="folder-root"]}})
    engine = Engine(store,drive)
    identity = store.create_job(drive.auth.project,"","root","Archive",str(root),workers)
    return drive, engine, identity


def run(engine, identity):
    engine.start(identity)
    engine.thread.join(15)
    assert not engine.thread.is_alive()
    return engine.store.job(identity)


def test_original_jpeg_exif_and_gps_are_preserved(environment):
    from PIL import Image
    store, root = environment
    exif = Image.Exif()
    exif[34665] = {36867: "2020:05:06 07:08:09"}
    exif[34853] = {1:"N",2:(1.,2.,3.),3:"E",4:(4.,5.,6.)}
    buffer=io.BytesIO()
    Image.new("RGB",(32,32),(60,120,180)).save(buffer,format="JPEG",exif=exif)
    original=buffer.getvalue()
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",original)])
    assert run(engine,identity)["status"]=="complete"
    assert (root/"photo.jpg").read_bytes()==original
    with Image.open(root/"photo.jpg") as saved:
        assert saved.getexif().get_ifd(34853)[1]=="N"
        assert saved.getexif().get_ifd(34665)[36867]=="2020:05:06 07:08:09"


def test_existing_different_file_is_never_overwritten(environment):
    store,root=environment;root.mkdir();(root/"photo.jpg").write_bytes(b"private original")
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    assert run(engine,identity)["status"]=="complete"
    assert (root/"photo.jpg").read_bytes()==b"private original"
    assert len(list(root.glob("photo~*.jpg")))==1


def test_existing_matching_files_are_checked_before_download(environment):
    store,root=environment;root.mkdir();(root/"one.jpg").write_bytes(b"one")
    drive,engine,identity=setup_job(store,root,[("one","one.jpg",b"one"),("two","two.jpg",b"two")])
    original_content=drive.content
    def content(*args):
        assert engine.live["files_checked"]==2
        return original_content(*args)
    drive.content=content
    assert run(engine,identity)["status"]=="complete"
    assert drive.downloads==[("two",0)]


def test_case_collisions_and_reserved_names_remain_distinct(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("one","PHOTO.jpg",b"one"),("two","photo.jpg",b"two"),("three","CON.txt",b"three"),("four","../../escape.jpg",b"four")])
    assert run(engine,identity)["status"]=="complete"
    files=[p for p in root.iterdir() if p.is_file()]
    assert len(files)==4
    assert len({p.name.casefold() for p in files})==4
    assert {p.read_bytes() for p in files}=={b"one",b"two",b"three",b"four"}


def seed_partial(store,root,engine,identity,content,amount):
    job=store.job(identity)
    root.mkdir(exist_ok=True)
    engine._scan(job)
    with store.db() as db:
        row=dict(db.execute("SELECT * FROM files WHERE job=?",(identity,)).fetchone())
        db.execute("UPDATE files SET bytes=?,part_sha=? WHERE job=? AND id=?",(amount,hashlib.sha256(content[:amount]).hexdigest(),identity,row["id"]))
    stamp=hashlib.sha256(fingerprint(json.loads(row["metadata"])).encode()).hexdigest()[:16]
    part=root/".diskdownloader"/(identity+"-"+hashlib.sha256(row["id"].encode()).hexdigest()[:20]+"-"+stamp+".part")
    part.parent.mkdir()
    part.write_bytes(content[:amount])
    return part


def test_resume_uses_validated_prefix_and_range(environment):
    store,root=environment;content=b"0123456789"*100000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    seed_partial(store,root,engine,identity,content,300000)
    assert run(engine,identity)["status"]=="complete"
    assert drive.downloads==[("photo",300000)]
    assert (root/"photo.jpg").read_bytes()==content


def test_ignored_range_restarts_own_partial_safely(environment):
    store,root=environment;content=b"abcdef"*100000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    seed_partial(store,root,engine,identity,content,300000);drive.ignored=True
    assert run(engine,identity)["status"]=="complete"
    assert (root/"photo.jpg").read_bytes()==content


def test_invalid_range_never_publishes_file(environment):
    store,root=environment;content=b"abcdef"*100000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    part=seed_partial(store,root,engine,identity,content,300000);drive.wrong_range=True
    assert run(engine,identity)["status"]=="completed_with_issues"
    assert not (root/"photo.jpg").exists()
    assert part.read_bytes()==content[:300000]


def test_modified_partial_is_not_trusted_or_destroyed(environment):
    store,root=environment;content=b"abcdef"*100000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    part=seed_partial(store,root,engine,identity,content,300000);part.write_bytes(b"other"*60000)
    job=run(engine,identity)
    assert job["status"]=="paused" and job["error"]=="partial_changed"
    assert part.read_bytes()==b"other"*60000
    assert not drive.downloads


def test_cloud_change_uses_new_partial_and_keeps_old_one(environment):
    store,root=environment;content=b"old content"*10000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    old=seed_partial(store,root,engine,identity,content,1000)
    new=b"new content"*10000
    drive.objects["photo"]=(metadata("photo","photo.jpg",new,version="2"),new)
    assert run(engine,identity)["status"]=="complete"
    assert drive.downloads==[("photo",0)]
    assert old.read_bytes()==content[:1000]
    assert (root/"photo.jpg").read_bytes()==new


def test_checksum_mismatch_never_becomes_complete(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    drive.objects["photo"][0]["sha256Checksum"]="0"*64
    assert run(engine,identity)["status"]=="completed_with_issues"
    assert not (root/"photo.jpg").exists()


def test_low_space_pauses_and_preserves_partial(environment,monkeypatch):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    monkeypatch.setattr("diskdownloader.engine.shutil.disk_usage",lambda p:types.SimpleNamespace(free=0))
    job=run(engine,identity)
    assert job["status"]=="paused" and job["error"]=="low_space"
    assert list((root/".diskdownloader").glob("*.part"))


def test_scanning_resumes_saved_page_without_reading_logs(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("one","one.jpg",b"one"),("two","two.jpg",b"two")])
    one=drive.objects["one"][0];two=drive.objects["two"][0]
    drive.folders={("folder-root",""):{"files":[one],"nextPageToken":"page2"},("folder-root","page2"):{"files":[two]}}
    original=drive.children
    def paused_children(folder,page=""):
        if page=="page2":raise Interrupted()
        return original(folder,page)
    drive.children=paused_children
    assert run(engine,identity)["status"]=="paused"
    assert drive.listings==[("folder-root","")]
    drive.children=original
    root.mkdir(exist_ok=True);(root/"old-log.html").write_bytes(b"not a downloaded photo")
    assert run(engine,identity)["status"]=="complete"
    assert drive.listings==[("folder-root",""),("folder-root","page2")]
    assert engine.live["files_checked"]==2


def test_wrong_account_cannot_continue_existing_task(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    with store.db() as db:db.execute("UPDATE jobs SET account='other' WHERE id=?",(identity,))
    job=run(engine,identity)
    assert job["error"]=="different_account"
    assert not drive.downloads


def test_google_documents_and_shortcuts_are_reported(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[])
    doc=metadata("doc","Document",mime="application/vnd.google-apps.document")
    drive.objects["doc"]=(doc,b"");drive.folders[("folder-root","")]["files"].append(doc)
    assert run(engine,identity)["status"]=="completed_with_issues"
    assert not drive.downloads


def test_internal_folder_name_is_not_used_for_cloud_content(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("photo",".diskdownloader",b"cloud")])
    assert run(engine,identity)["status"]=="complete"
    assert (root/".diskdownloader").is_dir()
    assert len(list(root.glob(".diskdownloader~*")))==1


def test_store_restart_recovers_active_status_without_losing_records(environment):
    store,root=environment
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    engine._scan(store.job(identity))
    store.change_job(identity,status="running")
    with store.db() as db:db.execute("UPDATE files SET status='downloading'")
    recovered=Store(store.path.parent)
    assert recovered.job(identity)["status"]=="interrupted"
    with recovered.db() as db:
        assert db.execute("SELECT status FROM files").fetchone()[0]=="pending"
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_empty_cloud_folders_are_created_without_overwriting_local_files(environment):
    store,root=environment;root.mkdir();(root/'Empty').write_bytes(b'keep this')
    drive,engine,identity=setup_job(store,root,[])
    empty=metadata('empty','Empty',mime=FOLDER)
    drive.objects['empty']=(empty,b'')
    drive.folders[('folder-root','')]['files'].append(empty)
    drive.folders[('empty','')]={'files':[]}
    assert run(engine,identity)['status']=='complete'
    assert (root/'Empty').read_bytes()==b'keep this'
    assert any(p.is_dir() for p in root.glob('Empty~*'))


def test_symlink_destination_is_rejected(environment,tmp_path):
    store,root=environment;outside=tmp_path/'outside';outside.mkdir();root.mkdir()
    try:(root/'photo.jpg').symlink_to(outside/'private.jpg')
    except OSError:pytest.skip('symlink privilege unavailable')
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",b"cloud")])
    job=run(engine,identity)
    assert job['error']=='unsafe_path'
    assert not (outside/'private.jpg').exists()


def test_hardlinked_partial_is_not_modified(environment,tmp_path):
    store,root=environment;content=b'abcdef'*100000
    drive,engine,identity=setup_job(store,root,[("photo","photo.jpg",content)])
    part=seed_partial(store,root,engine,identity,content,300000)
    import os
    os.link(part,tmp_path/'other-copy')
    job=run(engine,identity)
    assert job['error']=='unsafe_path'
    assert (tmp_path/'other-copy').read_bytes()==content[:300000]
