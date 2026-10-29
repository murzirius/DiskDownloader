import hashlib
import json
import threading

import keyring
import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .errors import AppError

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def client_config(data):
    if not isinstance(data, dict) or not isinstance(data.get("installed"), dict):
        raise AppError("desktop_json_required")
    config = data["installed"]
    client = config.get("client_id", "")
    project = config.get("project_id", "")
    if not isinstance(client, str) or not client.endswith(".apps.googleusercontent.com") or len(client) > 200:
        raise AppError("desktop_json_required")
    if not isinstance(project, str) or not project or len(project) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in project):
        raise AppError("desktop_json_required")
    # Uploaded JSON must never select a third-party token or authorization server.
    if config.get("token_uri") != TOKEN_URI or config.get("auth_uri") not in (AUTH_URI, "https://accounts.google.com/o/oauth2/v2/auth"):
        raise AppError("desktop_json_required")
    secret = config.get("client_secret", "")
    if not isinstance(secret, str) or not secret or len(secret) > 300:
        raise AppError("desktop_json_required")
    return {"installed": {"client_id": client, "project_id": project, "client_secret": secret,
                           "auth_uri": AUTH_URI, "token_uri": TOKEN_URI,
                           "redirect_uris": ["http://localhost"]}}


class Auth:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.vault_lock = threading.Lock()
        self.credentials = None
        self.config = None
        self.pending = False
        self.error = ""
        self.persistent = False
        self.epoch = 0
        profile = store.get("profile")
        if profile:
            try:
                saved = keyring.get_password("DiskDownloader", profile)
                if saved:
                    saved = json.loads(saved)
                    self.config = client_config(saved["config"])
                    info = saved.get("credentials")
                    if info and info.get("token_uri") == TOKEN_URI:
                        self.credentials = Credentials.from_authorized_user_info(info, [SCOPE])
                    self.persistent = True
            except Exception:
                self.error = "credential_store_unavailable"

    @property
    def project(self):
        return self.config["installed"]["project_id"] if self.config else ""

    def public(self):
        with self.lock:
            return {"project": self.project, "connected": self.credentials is not None,
                    "configured": self.config is not None, "pending": self.pending,
                    "persistent": self.persistent, "error": self.error}

    def _save(self):
        with self.lock:
            if self.config is None:
                return
            epoch = self.epoch
            profile = hashlib.sha256(self.config["installed"]["client_id"].encode()).hexdigest()
            value = {"config": self.config, "credentials": json.loads(self.credentials.to_json()) if self.credentials else None}
        try:
            with self.vault_lock:
                with self.lock:
                    if epoch != self.epoch:
                        return
                keyring.set_password("DiskDownloader", profile, json.dumps(value))
                with self.lock:
                    if epoch == self.epoch:
                        self.store.set("profile", profile)
                        self.persistent = True
        except Exception:
            # Never fall back to a plaintext token file.
            with self.lock:
                if epoch == self.epoch:
                    self.persistent = False

    def configure(self, data):
        config = client_config(data)
        with self.lock:
            if self.pending:
                raise AppError("auth_pending")
            self.epoch += 1
            self.config, self.credentials = config, None
            self.error = ""
        self._save()

    def connect(self, language="en"):
        with self.lock:
            if not self.config:
                raise AppError("desktop_json_required")
            if self.pending:
                return
            self.pending, self.error, self.credentials = True, "", None
            config, epoch = self.config, self.epoch

        def authorize():
            credentials, error = None, ""
            try:
                flow = InstalledAppFlow.from_client_config(config, [SCOPE], autogenerate_code_verifier=True)
                credentials = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180,
                    authorization_prompt_message="", success_message="Google Диск подключён к DiskDownloader. Эту вкладку можно закрыть." if language == "ru" else "Connected to DiskDownloader. You can close this tab.",
                    access_type="offline", prompt="consent")
                if not credentials.has_scopes([SCOPE]):
                    raise AppError("drive_permission_required", source="google")
            except AppError as exc:
                credentials, error = None, exc.code
            except Exception:
                credentials, error = None, "google_signin_failed"
            with self.lock:
                if self.epoch == epoch:
                    self.credentials, self.error, self.pending = credentials, error, False
            if credentials and self.epoch == epoch:
                self._save()
        threading.Thread(target=authorize, name="google-signin", daemon=True).start()

    def token(self):
        with self.refresh_lock:
            with self.lock:
                credentials, epoch = self.credentials, self.epoch
            if not credentials:
                raise AppError("connect_google", source="google")
            if not credentials.valid:
                try:
                    session = requests.Session()
                    try:
                        # Bound refresh requests, including when all download workers wait for refresh.
                        request = Request(session=session)
                        credentials.refresh(lambda **kw: request(**{**kw, "timeout": 20}))
                    finally:
                        session.close()
                    self._save()
                except RefreshError:
                    with self.lock:
                        if epoch == self.epoch:
                            self.error = "google_signin_expired"
                    raise AppError("google_signin_expired", source="google") from None
                except (TransportError, requests.RequestException):
                    raise AppError("internet_unavailable", retryable=True, source="internet") from None
            with self.lock:
                if epoch != self.epoch:
                    raise AppError("connect_google", source="google")
                self.error = ""
            return credentials.token

    def invalidate(self):
        with self.lock:
            self.error = "google_signin_expired"
            if self.credentials:
                self.credentials.token = None

    def disconnect(self):
        # Reject/discard in-flight sign-in and refresh results before clearing the vault.
        with self.lock:
            profile = self.store.get("profile")
            self.epoch += 1
            self.config = self.credentials = None
            self.pending = self.persistent = False
            self.store.set("profile", None)
            self.error = ""
        if profile:
            try:
                with self.vault_lock:
                    keyring.delete_password("DiskDownloader", profile)
            except Exception:
                raise AppError("credential_store_unavailable") from None
