import csv
import io
import json
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import __version__
from .config import system_language
from .drive import file_id
from .errors import AppError
from .quota import DEFAULTS


def create_app(store, auth, drive, engine, *, origin="http://127.0.0.1:8767", shutdown=None, picker=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, JSON_SORT_KEYS=False, APP_ORIGIN=origin)
    csrf = secrets.token_urlsafe(32)
    messages = {lang: json.loads((Path(__file__).parent / "locales" / (lang + ".json")).read_text(encoding="utf-8")) for lang in ("en", "ru")}
    app.extensions["csrf"] = csrf
    app.extensions["picker"] = {"pending": False, "path": "", "error": ""}

    @app.before_request
    def secure_request():
        current_origin = app.config["APP_ORIGIN"]
        if request.host != current_origin.split("://", 1)[1] or request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify(error="local_request_required"), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Origin") not in (None, current_origin) or not secrets.compare_digest(request.headers.get("X-App-Token", ""), csrf):
                return jsonify(error="local_request_required"), 403

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store" if not request.path.startswith("/static/") else "public, max-age=3600"
        return response

    @app.errorhandler(AppError)
    def known_error(exc):
        return jsonify(error=exc.code, source=exc.source, retryable=exc.retryable), 409 if exc.code == "task_active" else 400

    @app.errorhandler(Exception)
    def unknown_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error="invalid_request"), exc.code
        # Never return exception messages containing tokens, local paths or Google responses.
        return jsonify(error="application_failed", source="application"), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise AppError("invalid_request")
        return value

    def language():
        configured = store.get("language", "auto")
        return configured if configured in messages else request.accept_languages.best_match(["en", "ru"]) or system_language()

    @app.get("/")
    def index():
        configured = store.get("language", "auto")
        lang = language()
        return render_template("index.html", text=messages[lang], lang=lang, language=configured,
                               csrf=csrf, version=__version__)

    @app.get("/api/state")
    def state():
        project = auth.project
        query = urlencode({"project": project}) if project else ""
        return jsonify(auth=auth.public(), jobs=store.overview(), active=engine.active if engine.busy() else None,
                       session=dict(engine.live), quota=drive.quota.snapshot(project), picker=app.extensions["picker"],
                       console="https://console.cloud.google.com/apis/api/drive.googleapis.com/quotas?" + query)

    @app.post("/api/settings")
    def settings():
        data = body()
        if "language" in data:
            if data["language"] not in ("auto", "en", "ru"):
                raise AppError("invalid_request")
            store.set("language", data["language"])
        if "limits" in data:
            values = data["limits"]
            if not isinstance(values, dict) or set(values) != set(DEFAULTS) or any(type(v) is not int or v < 1 or v >= 2 ** 53 for v in values.values()):
                raise AppError("invalid_request")
            store.set("limits:" + auth.project, values)
        return jsonify(ok=True)

    @app.post("/api/client")
    def upload_client():
        if engine.busy():
            raise AppError("task_active")
        uploaded = request.files.get("file")
        try:
            data = json.loads(uploaded.read().decode("utf-8-sig")) if uploaded else None
        except (ValueError, UnicodeError):
            raise AppError("desktop_json_required") from None
        auth.configure(data)
        return jsonify(ok=True)

    @app.post("/api/connect")
    def connect():
        if engine.busy():
            raise AppError("task_active")
        auth.connect(language())
        return jsonify(ok=True)

    @app.post("/api/disconnect")
    def disconnect():
        if engine.busy():
            raise AppError("task_active")
        auth.disconnect()
        return jsonify(ok=True)

    @app.get("/api/files")
    def files():
        identity = file_id(request.args.get("folder", "root"))
        page = request.args.get("page", "")
        if len(page) > 4096:
            raise AppError("invalid_request")
        result = drive.children(identity, page)
        if result.get("incompleteSearch"):
            raise AppError("google_incomplete_list", source="google")
        return jsonify(result)

    @app.post("/api/jobs")
    def create_job():
        if engine.busy():
            raise AppError("task_active")
        connection = auth.public()
        if not connection["connected"] or connection.get("pending") or connection.get("error"):
            raise AppError("connect_google", source="google")
        data = body()
        source = file_id(data.get("source", "root"))
        root = data.get("root")
        workers = data.get("workers", 4)
        name = data.get("name", "Google Drive")
        if not isinstance(root, str) or not root.strip() or len(root) > 10000 or "\x00" in root or not Path(root).is_absolute():
            raise AppError("choose_folder", source="local")
        if type(workers) is not int or not 1 <= workers <= 8 or not isinstance(name, str) or len(name) > 500:
            raise AppError("invalid_request")
        identity = store.create_job(auth.project, "", source, name, str(Path(root).absolute()), workers)
        engine.start(identity)
        return jsonify(id=identity)

    @app.post("/api/jobs/<identity>/<action>")
    def job_action(identity, action):
        if not store.job(identity):
            raise AppError("task_missing")
        if action == "resume":
            engine.start(identity)
        elif action in ("pause", "stop"):
            engine.pause(identity, action == "stop")
        else:
            raise AppError("invalid_request")
        return jsonify(ok=True)

    @app.get("/api/jobs/<identity>/report.csv")
    def report(identity):
        if not store.job(identity):
            raise AppError("task_missing")
        localized = messages[language()]
        def generate():
            yield "\ufeff"
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["file", "status", "bytes", "sha256", "error", "explanation"])
            yield buffer.getvalue()
            last = ""
            while True:
                with store.db() as db:
                    rows = db.execute("SELECT id,local,status,bytes,sha,error FROM files WHERE job=? AND id>? ORDER BY id LIMIT 256", (identity, last)).fetchall()
                if not rows:
                    break
                buffer.seek(0)
                buffer.truncate()
                for row in rows:
                    # CSV formula injection protection for cloud-controlled filenames.
                    name = row["local"]
                    writer.writerow(["'" + name if name.startswith(("=", "+", "-", "@", "\t", "\r")) else name, row["status"], row["bytes"], row["sha"], row["error"], localized.get(row["error"], row["error"])])
                    last = row["id"]
                yield buffer.getvalue()
        return Response(generate(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=DiskDownloader-report.csv"})

    @app.post("/api/pick-folder")
    def pick_folder():
        state = app.extensions["picker"]
        if not state["pending"]:
            state.update(pending=True, path="", error="")
            def choose():
                try:
                    if not picker:
                        raise RuntimeError()
                    state["path"] = picker() or ""
                except Exception:
                    state["error"] = "folder_picker_failed"
                finally:
                    state["pending"] = False
            threading.Thread(target=choose, name="folder-picker", daemon=True).start()
        return jsonify(ok=True)

    @app.post("/api/shutdown")
    def exit_app():
        if shutdown:
            threading.Thread(target=shutdown, name="shutdown", daemon=True).start()
        return jsonify(ok=True)

    return app
