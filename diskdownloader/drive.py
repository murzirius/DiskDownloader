import re
import threading
from urllib.parse import quote, urljoin, urlsplit

import requests

from .errors import AppError

FILE_FIELDS = "id,name,mimeType,size,md5Checksum,sha256Checksum,modifiedTime,version,capabilities(canDownload),shortcutDetails(targetId,targetMimeType)"
FOLDER = "application/vnd.google-apps.folder"


def file_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise AppError("invalid_file_id")
    return value


class Drive:
    BASE = "https://www.googleapis.com/drive/v3/"

    def __init__(self, auth, quota):
        self.auth, self.quota = auth, quota
        self.local = threading.local()

    def request(self, path, *, params=None, headers=None, stream=False):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        session = self.local.session
        project = self.auth.project
        units = 100 if path == "files" else 5
        token = self.auth.token()
        self.quota.record(project, units=units, requests=1)
        try:
            request_headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "identity", **(headers or {})}
            url = self.BASE + path
            response = session.get(url, params=params, headers=request_headers,
                timeout=(10, 20), stream=stream, allow_redirects=False)
            for redirect in range(4):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                url = urljoin(response.url, response.headers.get("Location", ""))
                response.close()
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password or not (
                    host in ("www.googleapis.com", "content.googleapis.com", "drive.usercontent.google.com") or host.endswith(".googleusercontent.com")):
                    raise AppError("google_invalid_response", source="google")
                if host not in ("www.googleapis.com", "content.googleapis.com"):
                    request_headers.pop("Authorization", None)
                response = session.get(url, headers=request_headers, timeout=(10, 20), stream=stream, allow_redirects=False)
        except requests.RequestException:
            raise AppError("internet_unavailable", retryable=True, source="internet") from None
        if response.status_code not in (200, 206):
            code, reasons = response.status_code, set()
            try:
                body = response.json().get("error", {})
                reasons = {e.get("reason") for e in body.get("errors", []) if isinstance(e, dict)}
            except (ValueError, AttributeError, TypeError):
                pass
            response.close()
            if code == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded", "downloadQuotaExceeded", "dailyLimitExceeded"}:
                self.quota.record(project, limited=1)
                raise AppError("google_quota", retryable=True, source="google")
            if code == 401:
                raise AppError("google_signin_expired", source="google")
            if code == 403:
                raise AppError("google_download_denied", source="google")
            if code == 404:
                raise AppError("google_file_missing", source="google")
            if code == 416:
                raise AppError("range_invalid", source="google")
            raise AppError("google_unavailable", retryable=code >= 500, source="google")
        return response

    def json(self, path, **params):
        response = self.request(path, params=params)
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, requests.RequestException):
            raise AppError("google_invalid_response", retryable=True, source="google") from None
        finally:
            response.close()

    def get(self, identity):
        return self.json("files/" + quote(file_id(identity), safe=""), fields=FILE_FIELDS, supportsAllDrives="true")

    def children(self, identity, page=""):
        return self.json("files", q="'" + file_id(identity) + "' in parents and trashed=false", pageSize=1000,
                         pageToken=page or None, fields="nextPageToken,incompleteSearch,files(" + FILE_FIELDS + ")",
                         supportsAllDrives="true", includeItemsFromAllDrives="true")

    def account(self):
        return self.json("about", fields="user(displayName,emailAddress,permissionId),storageQuota")

    def content(self, identity, offset):
        return self.request("files/" + quote(file_id(identity), safe=""),
                            params={"alt": "media", "supportsAllDrives": "true"},
                            headers={"Range": "bytes=" + str(offset) + "-"} if offset else {}, stream=True)
