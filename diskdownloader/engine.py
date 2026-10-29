import concurrent.futures
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path

import requests

from .drive import FOLDER
from .errors import AppError, Interrupted
from .paths import component, suffix, safe_path, regular_open, publish, set_modified

MIN_FREE = 500 * 1024 * 1024


def fingerprint(meta):
    return json.dumps([meta.get(k) for k in ("id", "version", "size", "md5Checksum", "sha256Checksum", "modifiedTime")], separators=(",", ":"))


class Engine:
    def __init__(self, store, drive):
        self.store, self.drive = store, drive
        self.lock = threading.RLock()
        self.thread = None
        self.stop = threading.Event()
        self.active = None
        self.stop_status = "paused"
        self.failure = None
        self.live = {"files_checked": 0, "current": "", "network_bytes": 0, "started": 0}

    def busy(self):
        with self.lock:
            return self.thread is not None and self.thread.is_alive()

    def checkpoint(self):
        if self.stop.is_set():
            raise Interrupted()

    def start(self, identity):
        with self.lock:
            if self.busy():
                raise AppError("task_active")
            job = self.store.job(identity)
            if not job:
                raise AppError("task_missing")
            if job["project"] != self.drive.auth.project:
                raise AppError("different_account")
            self.stop.clear()
            self.failure, self.active, self.stop_status = None, identity, "paused"
            self.live = {"files_checked": 0, "current": "", "network_bytes": 0, "started": time.time()}
            self.store.change_job(identity, status="running", error="")
            self.thread = threading.Thread(target=self._run, args=(job,), name="archive-job", daemon=False)
            self.thread.start()

    def pause(self, identity, stopped=False):
        with self.lock:
            if self.active == identity and self.busy():
                self.stop_status = "stopped" if stopped else "paused"
                self.store.change_job(identity, status="stopping" if stopped else "pausing")
                self.stop.set()

    def _retry(self, function):
        for attempt in range(5):
            self.checkpoint()
            try:
                return function()
            except AppError as exc:
                if not exc.retryable or attempt == 4:
                    raise
                self.store.event(self.active or "", exc.code)
                if self.stop.wait(min(32, 2 ** attempt) + random.random()):
                    raise Interrupted()

    def _run(self, job):
        identity = job["id"]
        try:
            account = self._retry(self.drive.account)["user"]["permissionId"]
            if job["account"] and account != job["account"]:
                raise AppError("different_account", source="google")
            with self.store.db() as db:
                db.execute("UPDATE jobs SET account=? WHERE id=?", (account, identity))
            safe_path(job["root"], mkdir=True)
            if job["phase"] == "scan":
                self._scan(job)
            self.store.change_job(identity, phase="check")
            self._check_all(job)
            self.store.change_job(identity, phase="download")
            self._download_all(job)
            self.checkpoint()
            with self.store.db() as db:
                failed = db.execute("SELECT COUNT(*) FROM files WHERE job=? AND status IN ('failed','unsupported')", (identity,)).fetchone()[0]
            self.store.change_job(identity, status="completed_with_issues" if failed else "complete", phase="done", error="")
        except Interrupted:
            self.store.change_job(identity, status=self.stop_status, error=self.failure.code if self.failure else "")
        except AppError as exc:
            self.store.change_job(identity, status="paused", error=exc.code)
            self.store.event(identity, exc.code)
        except OSError:
            self.store.change_job(identity, status="paused", error="local_unavailable")
            self.store.event(identity, "local_unavailable")
        except Exception:
            self.store.change_job(identity, status="paused", error="application_failed")
            self.store.event(identity, "application_failed")
        finally:
            with self.store.db() as db:
                db.execute("UPDATE files SET status='pending' WHERE job=? AND status='downloading'", (identity,))

    def _allocate(self, db, job, parent, name, identity, root=None):
        base = component(name)
        for n in range(10000):
            leaf = base if n == 0 else suffix(base, identity + ":" + str(n))
            local = parent + "/" + leaf if parent else leaf
            folded = local.casefold()
            if folded == ".diskdownloader":
                continue
            if root is not None:
                path = safe_path(root, local)
                if os.path.lexists(path) and not path.is_dir():
                    continue
            if not db.execute("SELECT 1 FROM files WHERE job=? AND folded=? UNION ALL SELECT 1 FROM folders WHERE job=? AND folded=?", (job, folded, job, folded)).fetchone():
                return local
        raise AppError("name_collision", source="local")

    def _insert(self, db, job, parent, meta, root=None):
        identity, name = meta["id"], meta["name"]
        if db.execute("SELECT 1 FROM files WHERE job=? AND id=? UNION ALL SELECT 1 FROM folders WHERE job=? AND id=?", (job, identity, job, identity)).fetchone():
            return
        local = self._allocate(db, job, parent, name, identity, root if meta["mimeType"] == FOLDER else None)
        if meta["mimeType"] == FOLDER:
            db.execute("INSERT INTO folders(job,id,local,folded) VALUES(?,?,?,?)", (job, identity, local, local.casefold()))
        else:
            unsupported = meta["mimeType"].startswith("application/vnd.google-apps.")
            db.execute("INSERT INTO files(job,id,local,folded,metadata,status,error) VALUES(?,?,?,?,?,?,?)",
                (job, identity, local, local.casefold(), json.dumps(meta), "unsupported" if unsupported else "pending", "google_document_unsupported" if unsupported else ""))

    def _scan(self, job):
        source = self._retry(lambda: self.drive.get(job["source"]))
        with self.store.db() as db:
            if source["mimeType"] == FOLDER:
                db.execute("INSERT OR IGNORE INTO folders(job,id,local,folded) VALUES(?,?,?,?)", (job["id"], source["id"], "", ""))
            else:
                self._insert(db, job["id"], "", source, job["root"])
        while True:
            self.checkpoint()
            with self.store.db() as db:
                folder = db.execute("SELECT * FROM folders WHERE job=? AND done=0 ORDER BY rowid LIMIT 1", (job["id"],)).fetchone()
            if not folder:
                break
            try:
                page = self._retry(lambda: self.drive.children(folder["id"], folder["page"]))
            except AppError as exc:
                # An expired pagination token requires restarting this folder, not deleting discovered items.
                if exc.code == "google_unavailable" and folder["page"]:
                    with self.store.db() as db:
                        db.execute("UPDATE folders SET page='' WHERE job=? AND id=?", (job["id"], folder["id"]))
                raise
            if page.get("incompleteSearch") or not isinstance(page.get("files"), list):
                raise AppError("google_incomplete_list", source="google")
            next_page = page.get("nextPageToken", "")
            if next_page and next_page == folder["page"]:
                raise AppError("google_incomplete_list", source="google")
            with self.store.db() as db:
                for meta in page["files"]:
                    self.checkpoint()
                    self._insert(db, job["id"], folder["local"], meta, job["root"])
                db.execute("UPDATE folders SET page=?,done=? WHERE job=? AND id=?", (next_page, not next_page, job["id"], folder["id"]))
            self.live["current"] = folder["local"]
        self.store.change_job(job["id"], phase="check")

    def _hash(self, path, limit=None):
        sha = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        size = 0
        with regular_open(path) as stream:
            while limit is None or size < limit:
                self.checkpoint()
                data = stream.read(min(1024 * 1024, limit - size) if limit is not None else 1024 * 1024)
                if not data:
                    break
                size += len(data)
                sha.update(data)
                md5.update(data)
        return size, sha, md5

    def _matches(self, path, meta, own_sha=""):
        if not path.is_file() or path.stat().st_size != int(meta.get("size", -1)):
            return False
        if not (meta.get("sha256Checksum") or meta.get("md5Checksum") or own_sha):
            return False
        size, sha, md5 = self._hash(path)
        if meta.get("sha256Checksum"):
            return sha.hexdigest() == meta["sha256Checksum"]
        if meta.get("md5Checksum"):
            return md5.hexdigest() == meta["md5Checksum"]
        return sha.hexdigest() == own_sha

    def _check_all(self, job):
        # Preserve empty folders too; do not scan unrelated filesystem entries or reports.
        folder_cursor = 0
        while True:
            with self.store.db() as db:
                folders = db.execute("SELECT rowid,local FROM folders WHERE job=? AND rowid>? ORDER BY rowid LIMIT 128", (job["id"], folder_cursor)).fetchall()
            if not folders:
                break
            for folder in folders:
                self.checkpoint()
                folder_cursor = folder["rowid"]
                path = safe_path(job["root"], folder["local"])
                path.mkdir(parents=True, exist_ok=True)
                safe_path(job["root"], folder["local"])
        last = ""
        while True:
            self.checkpoint()
            with self.store.db() as db:
                rows = [dict(r) for r in db.execute("SELECT * FROM files WHERE job=? AND id>? ORDER BY id LIMIT 128", (job["id"], last))]
            if not rows:
                break
            for row in rows:
                self.checkpoint()
                last = row["id"]
                if row["status"] == "unsupported":
                    continue
                meta = json.loads(row["metadata"])
                path = safe_path(job["root"], row["local"])
                matched = self._matches(path, meta, row["sha"])
                with self.store.db() as db:
                    db.execute("UPDATE files SET status=?,error='',bytes=? WHERE job=? AND id=?", (
                        "complete" if matched else "pending", int(meta["size"]) if matched else row["bytes"] if path.exists() is False else 0,
                        job["id"], row["id"]))
                self.live["files_checked"] += 1
                self.live["current"] = row["local"]

    def _download_all(self, job):
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=job["workers"], thread_name_prefix="download")
        pending = set()
        try:
            while True:
                self.checkpoint()
                available = job["workers"] * 2 - len(pending)
                if available:
                    with self.store.db() as db:
                        rows = [dict(r) for r in db.execute("SELECT * FROM files WHERE job=? AND status='pending' LIMIT ?", (job["id"], available))]
                        for row in rows:
                            db.execute("UPDATE files SET status='downloading' WHERE job=? AND id=?", (job["id"], row["id"]))
                    for row in rows:
                        pending.add(executor.submit(self._file, job, row))
                if not pending:
                    break
                done, pending = concurrent.futures.wait(pending, timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    future.result()
        except (AppError, OSError) as exc:
            self.failure = exc if isinstance(exc, AppError) else AppError("local_unavailable", source="local")
            self.stop.set()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def _file(self, job, row):
        try:
            self._retry(lambda: self._transfer(job, row))
        except AppError as exc:
            if exc.retryable or exc.code in ("google_signin_expired", "connect_google", "low_space", "different_account") or exc.source == "local":
                raise
            with self.store.db() as db:
                db.execute("UPDATE files SET status='failed',error=? WHERE job=? AND id=?", (exc.code, job["id"], row["id"]))
            self.store.event(job["id"], exc.code)

    def _transfer(self, job, row):
        self.checkpoint()
        with self.store.db() as db:
            row = dict(db.execute("SELECT * FROM files WHERE job=? AND id=?", (job["id"], row["id"])).fetchone())
        meta = self.drive.get(row["id"])
        if meta.get("capabilities", {}).get("canDownload") is False:
            raise AppError("google_download_denied", source="google")
        if meta.get("mimeType", "").startswith("application/vnd.google-apps."):
            raise AppError("google_document_unsupported", source="google")
        expected = int(meta.get("size", -1))
        if expected < 0 or expected >= 2 ** 63:
            raise AppError("google_invalid_response", source="google")
        if fingerprint(meta) != fingerprint(json.loads(row["metadata"])):
            row.update(metadata=json.dumps(meta), bytes=0, part_sha="", sha="")
            with self.store.db() as db:
                db.execute("UPDATE files SET metadata=?,bytes=0,part_sha='',sha='' WHERE job=? AND id=?", (row["metadata"], job["id"], row["id"]))
        target = safe_path(job["root"], row["local"], mkdir=True)
        if os.path.lexists(target):
            if self._matches(target, meta, row["sha"]):
                with self.store.db() as db:
                    db.execute("UPDATE files SET status='complete',bytes=? WHERE job=? AND id=?", (expected, job["id"], row["id"]))
                return
            parent, _, name = row["local"].rpartition("/")
            with self.store.db() as db:
                local = self._allocate(db, job["id"], parent, suffix(name, row["id"] + ":existing"), row["id"])
                db.execute("UPDATE files SET local=?,folded=? WHERE job=? AND id=?", (local, local.casefold(), job["id"], row["id"]))
            row["local"] = local
            target = safe_path(job["root"], local, mkdir=True)
            if os.path.lexists(target):
                raise AppError("name_collision", source="local")
        stamp = hashlib.sha256(fingerprint(meta).encode()).hexdigest()[:16]
        relative_part = ".diskdownloader/" + job["id"] + "-" + hashlib.sha256(row["id"].encode()).hexdigest()[:20] + "-" + stamp + ".part"
        part = safe_path(job["root"], relative_part, mkdir=True)
        if not part.exists():
            with regular_open(part, "xb"):
                pass
            row.update(bytes=0, part_sha="")
        offset = row["bytes"]
        size, sha, md5 = self._hash(part, offset)
        if size != offset or (offset and sha.hexdigest() != row["part_sha"]) or offset > expected:
            raise AppError("partial_changed", source="local")
        with regular_open(part, "r+b") as stream:
            stream.truncate(offset)
            stream.seek(offset)
            response = None
            byte_delta = 0
            last_flush = time.monotonic()

            def flush():
                nonlocal byte_delta, last_flush
                stream.flush()
                os.fsync(stream.fileno())
                with self.store.db() as db:
                    db.execute("UPDATE files SET bytes=?,part_sha=? WHERE job=? AND id=?", (offset, sha.hexdigest(), job["id"], row["id"]))
                if byte_delta:
                    self.drive.quota.record(job["project"], bytes_count=byte_delta)
                    with self.lock:
                        self.live["network_bytes"] += byte_delta
                byte_delta, last_flush = 0, time.monotonic()

            try:
                if offset < expected:
                    response = self.drive.content(row["id"], offset)
                    if offset and response.status_code == 200:
                        offset = 0
                        sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
                        stream.seek(0)
                        stream.truncate()
                        flush()
                    elif response.status_code == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not match or tuple(map(int, match.groups())) != (offset, expected - 1, expected):
                            raise AppError("range_invalid", source="google")
                    for chunk in response.iter_content(256 * 1024):
                        self.checkpoint()
                        if not chunk:
                            continue
                        byte_delta += len(chunk)
                        if offset + len(chunk) > expected:
                            raise AppError("integrity_failed", source="google")
                        if shutil.disk_usage(part.parent).free < MIN_FREE + len(chunk):
                            raise AppError("low_space", source="local")
                        stream.write(chunk)
                        sha.update(chunk)
                        md5.update(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last_flush >= 1.5:
                            flush()
                flush()
            except requests.RequestException:
                raise AppError("internet_unavailable", retryable=True, source="internet") from None
            finally:
                if response is not None:
                    response.close()
                flush()
        if offset < expected:
            raise AppError("transfer_incomplete", retryable=True, source="internet")
        if offset != expected or (meta.get("sha256Checksum") and sha.hexdigest() != meta["sha256Checksum"]) or (meta.get("md5Checksum") and md5.hexdigest() != meta["md5Checksum"]):
            raise AppError("integrity_failed", source="google")
        if fingerprint(self.drive.get(row["id"])) != fingerprint(meta):
            raise AppError("source_changed", retryable=True, source="google")
        self.checkpoint()
        with self.store.db() as db:
            db.execute("UPDATE files SET sha=? WHERE job=? AND id=?", (sha.hexdigest(), job["id"], row["id"]))
        safe_path(job["root"], row["local"])
        safe_path(job["root"], relative_part)
        publish(part, target)
        if meta.get("modifiedTime"):
            try:
                stamp_time = datetime.datetime.fromisoformat(meta["modifiedTime"].replace("Z", "+00:00")).timestamp()
                set_modified(target, stamp_time)
            except (OSError, ValueError, OverflowError):
                self.store.event(job["id"], "timestamp_failed")
        with self.store.db() as db:
            db.execute("UPDATE files SET status='complete',bytes=?,error='' WHERE job=? AND id=?", (offset, job["id"], row["id"]))
