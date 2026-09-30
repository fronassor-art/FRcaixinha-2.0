"""Portable encrypted backup envelope and narrowly scoped Google Drive transport.

Google is an off-VM transport only. The encrypted Phase A directory remains the
canonical restore input and this module never changes its authenticated manifest.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tarfile
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

import httpx

from app import data_protection as protection

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 100 * 1024**3
CHUNK_BYTES = 8 * 1024**2
MAX_RETRIES = 4
_APP_MARKER = "frcaixinha_backup_v1"
_BACKUP_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\Z")


class DriveBackupError(RuntimeError):
    """A non-sensitive, stable error code suitable for logs and CLI output."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _private_regular_file(path: Path, error: str) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise DriveBackupError(error) from None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise DriveBackupError(error)


def _private_directory(path: Path, *, create: bool = False) -> None:
    if create and not path.exists():
        path.mkdir(mode=0o700, parents=True)
    try:
        info = path.lstat()
    except OSError:
        raise DriveBackupError("private_directory_invalid") from None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077:
        raise DriveBackupError("private_directory_invalid")


def _read_manifest(directory: Path) -> dict:
    _private_directory(directory)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise DriveBackupError("manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DriveBackupError("manifest_invalid") from None
    if (not isinstance(manifest, dict)
            or manifest.get("manifest_version") != protection.MANIFEST_VERSION
            or manifest.get("status") != "COMPLETE_LOCAL"
            or manifest.get("off_vm_status") != "NOT_UPLOADED"
            or not isinstance(manifest.get("backup_id"), str)
            or not _BACKUP_ID.fullmatch(manifest["backup_id"])):
        raise DriveBackupError("backup_not_complete_local")
    return manifest


def _validated_local_files(directory: Path, manifest: dict) -> list[tuple[Path, str]]:
    archive = manifest.get("archive")
    evidence = manifest.get("evidence")
    if not isinstance(archive, dict) or not isinstance(evidence, list):
        raise DriveBackupError("manifest_contract_invalid")
    files: list[tuple[Path, str]] = []
    expected: set[str] = {"manifest.json"}
    if archive.get("filename") != "postgres.dump.age":
        raise DriveBackupError("manifest_filename_invalid")
    archive_path = directory / "postgres.dump.age"
    protection._verify_file(directory, archive)
    files.append((archive_path, "postgres.dump.age"))
    expected.add("postgres.dump.age")
    if manifest.get("evidence_file_count") != len(evidence):
        raise DriveBackupError("evidence_count_mismatch")
    for ref in evidence:
        metadata = ref.get("encrypted") if isinstance(ref, dict) else None
        if not isinstance(metadata, dict):
            raise DriveBackupError("evidence_metadata_invalid")
        filename = metadata.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".age"):
            raise DriveBackupError("manifest_filename_invalid")
        relative = f"evidence/{filename}"
        path = directory / relative
        protection._verify_file(directory / "evidence", metadata)
        files.append((path, relative))
        expected.add(relative)
    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise DriveBackupError("backup_symlink_rejected")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
        elif not path.is_dir():
            raise DriveBackupError("backup_member_invalid")
    if actual != expected:
        raise DriveBackupError("backup_members_unexpected")
    files.append((directory / "manifest.json", "manifest.json"))
    return sorted(files, key=lambda item: item[1])


def inspect_envelope(envelope: Path, *, max_bytes: int = MAX_ENVELOPE_BYTES) -> dict:
    """Validate envelope members and embedded Phase A hashes without extracting."""
    if envelope.is_symlink() or not envelope.is_file() or envelope.stat().st_size > max_bytes:
        raise DriveBackupError("envelope_invalid")
    try:
        with tarfile.open(envelope, mode="r:") as archive:
            members = archive.getmembers()
            names = []
            total_size = 0
            for member in members:
                if (not _safe_member_name(member.name) or not member.isfile()
                        or member.issym() or member.islnk() or member.isdev() or member.size < 0):
                    raise DriveBackupError("envelope_member_invalid")
                names.append(member.name)
                total_size += member.size
                if total_size > max_bytes:
                    raise DriveBackupError("envelope_size_limit_exceeded")
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise DriveBackupError("envelope_members_invalid")
            manifest_member = next(member for member in members if member.name == "manifest.json")
            if manifest_member.size > 1024 * 1024:
                raise DriveBackupError("manifest_size_invalid")
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise DriveBackupError("manifest_invalid")
            manifest = json.loads(stream.read().decode("utf-8"))
            if (not isinstance(manifest, dict)
                    or manifest.get("manifest_version") != protection.MANIFEST_VERSION
                    or manifest.get("status") != "COMPLETE_LOCAL"
                    or manifest.get("off_vm_status") != "NOT_UPLOADED"
                    or not isinstance(manifest.get("backup_id"), str)
                    or not _BACKUP_ID.fullmatch(manifest["backup_id"])):
                raise DriveBackupError("backup_not_complete_local")
            expected = {"manifest.json": None, "postgres.dump.age": manifest.get("archive")}
            evidence = manifest.get("evidence")
            if not isinstance(evidence, list) or manifest.get("evidence_file_count") != len(evidence):
                raise DriveBackupError("evidence_count_mismatch")
            for entry in evidence:
                metadata = entry.get("encrypted", {}) if isinstance(entry, dict) else {}
                name = metadata.get("filename")
                if not isinstance(name, str) or Path(name).name != name or not name.endswith(".age"):
                    raise DriveBackupError("manifest_filename_invalid")
                expected[f"evidence/{name}"] = metadata
            if set(names) != set(expected):
                raise DriveBackupError("envelope_members_unexpected")
            for member in members:
                metadata = expected[member.name]
                if metadata is None:
                    continue
                if metadata.get("filename") != PurePosixPath(member.name).name or metadata.get("size") != member.size:
                    raise DriveBackupError("envelope_member_size_mismatch")
                content = archive.extractfile(member)
                if content is None:
                    raise DriveBackupError("envelope_member_invalid")
                digest = hashlib.sha256()
                for block in iter(lambda: content.read(1024 * 1024), b""):
                    digest.update(block)
                if digest.hexdigest() != metadata.get("sha256"):
                    raise DriveBackupError("envelope_member_checksum_mismatch")
    except DriveBackupError:
        raise
    except (OSError, tarfile.TarError, UnicodeDecodeError, ValueError, KeyError):
        raise DriveBackupError("envelope_invalid") from None
    return manifest


def create_envelope(backup_directory: Path, target: Path,
                    *, max_bytes: int = MAX_ENVELOPE_BYTES) -> dict:
    """Create a deterministic uncompressed tar envelope of a validated local set."""
    manifest = _read_manifest(backup_directory)
    files = _validated_local_files(backup_directory, manifest)
    if target.exists() or target.is_symlink():
        raise DriveBackupError("envelope_target_exists")
    _private_directory(target.parent, create=True)
    estimate = sum(path.stat().st_size for path, _ in files) + 1024 * (len(files) + 2)
    if estimate > max_bytes:
        raise DriveBackupError("envelope_size_limit_exceeded")
    try:
        with target.open("xb") as raw:
            os.chmod(target, 0o600)
            with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path, relative in files:
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.mode = 0o600
                    info.pax_headers = {}
                    with path.open("rb") as source:
                        archive.addfile(info, source)
            raw.flush()
            os.fsync(raw.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    size = target.stat().st_size
    if size > max_bytes:
        target.unlink(missing_ok=True)
        raise DriveBackupError("envelope_size_limit_exceeded")
    return {"envelope_version": ENVELOPE_VERSION, "backup_id": manifest["backup_id"],
            "filename": target.name, "size": size, "sha256": _sha256(target),
            "md5": _md5(target)}


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and bool(path.parts)
            and all(part not in {"", ".", ".."} for part in path.parts)
            and "\\" not in name and "\x00" not in name)


def extract_envelope(envelope: Path, target_directory: Path,
                     *, expected_sha256: str | None = None,
                     max_bytes: int = MAX_ENVELOPE_BYTES) -> Path:
    """Validate all tar metadata before writing members to a private directory."""
    if envelope.is_symlink() or not envelope.is_file() or envelope.stat().st_size > max_bytes:
        raise DriveBackupError("envelope_invalid")
    if expected_sha256 and _sha256(envelope) != expected_sha256:
        raise DriveBackupError("envelope_checksum_mismatch")
    if target_directory.exists() or target_directory.is_symlink():
        raise DriveBackupError("extract_target_exists")
    target_directory.mkdir(mode=0o700, parents=True)
    target_directory.chmod(0o700)
    try:
        with tarfile.open(envelope, mode="r:") as archive:
            members = archive.getmembers()
            names: list[str] = []
            total = 0
            for member in members:
                if (not _safe_member_name(member.name) or not member.isfile()
                        or member.issym() or member.islnk() or member.isdev() or member.size < 0):
                    raise DriveBackupError("envelope_member_invalid")
                names.append(member.name)
                total += member.size
                if total > max_bytes:
                    raise DriveBackupError("envelope_size_limit_exceeded")
            if not members or len(names) != len(set(names)) or "manifest.json" not in names:
                raise DriveBackupError("envelope_members_invalid")
            manifest_member = next(m for m in members if m.name == "manifest.json")
            if manifest_member.size > 1024 * 1024:
                raise DriveBackupError("manifest_size_invalid")
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise DriveBackupError("manifest_invalid")
            try:
                manifest = json.loads(stream.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise DriveBackupError("manifest_invalid") from None
            if (not isinstance(manifest, dict)
                    or manifest.get("manifest_version") != protection.MANIFEST_VERSION
                    or manifest.get("status") != "COMPLETE_LOCAL"
                    or manifest.get("off_vm_status") != "NOT_UPLOADED"):
                raise DriveBackupError("backup_not_complete_local")
            evidence = manifest.get("evidence")
            if not isinstance(evidence, list):
                raise DriveBackupError("manifest_contract_invalid")
            expected_names = {"manifest.json", "postgres.dump.age"}
            for item in evidence:
                metadata = item.get("encrypted", {}) if isinstance(item, dict) else {}
                name = metadata.get("filename")
                if not isinstance(name, str) or Path(name).name != name or not name.endswith(".age"):
                    raise DriveBackupError("manifest_filename_invalid")
                expected_names.add(f"evidence/{name}")
            if manifest.get("evidence_file_count") != len(evidence):
                raise DriveBackupError("evidence_count_mismatch")
            if set(names) != expected_names:
                raise DriveBackupError("envelope_members_unexpected")
            for member in members:
                out = target_directory.joinpath(*PurePosixPath(member.name).parts)
                out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not out.parent.resolve().is_relative_to(target_directory.resolve()):
                    raise DriveBackupError("envelope_path_invalid")
                source = archive.extractfile(member)
                if source is None:
                    raise DriveBackupError("envelope_member_invalid")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(out, flags, 0o600)
                with os.fdopen(descriptor, "wb") as sink, source:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
                out.chmod(0o600)
        _validated_local_files(target_directory, _read_manifest(target_directory))
    except Exception:
        shutil.rmtree(target_directory, ignore_errors=True)
        raise
    return target_directory


def _atomic_json(path: Path, value: dict) -> None:
    _private_directory(path.parent, create=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _backup_upload_lock(directory: Path, backup_id: str):
    if not _BACKUP_ID.fullmatch(backup_id):
        raise DriveBackupError("backup_id_invalid")
    _private_directory(directory)
    lock_path = directory / f".{backup_id}.upload.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DriveBackupError("upload_lock_invalid")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_google_config(path: Path) -> dict:
    _private_regular_file(path, "oauth_client_file_invalid")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DriveBackupError("oauth_client_file_invalid") from None
    if set(config) != {"installed"}:
        raise DriveBackupError("oauth_client_type_invalid")
    installed = config["installed"]
    if not isinstance(installed, dict) or not installed.get("client_id") or not installed.get("client_secret"):
        raise DriveBackupError("oauth_client_file_invalid")
    return config


def _save_credentials(credentials, token_path: Path) -> None:
    scopes = set(getattr(credentials, "granted_scopes", None) or credentials.scopes or [])
    if scopes != {DRIVE_FILE_SCOPE}:
        raise DriveBackupError("oauth_scope_mismatch")
    _private_directory(token_path.parent, create=True)
    _atomic_json(token_path, json.loads(credentials.to_json()))


def load_credentials(token_path: Path):
    from google.oauth2.credentials import Credentials

    _private_regular_file(token_path, "oauth_token_file_invalid")
    try:
        raw = json.loads(token_path.read_text(encoding="utf-8"))
        scopes = set(raw.get("scopes", []))
        granted = set(raw.get("granted_scopes", []))
        if scopes != {DRIVE_FILE_SCOPE} or (granted and granted != {DRIVE_FILE_SCOPE}):
            raise DriveBackupError("oauth_scope_mismatch")
        return Credentials.from_authorized_user_info(raw, scopes=[DRIVE_FILE_SCOPE])
    except DriveBackupError:
        raise
    except Exception:
        raise DriveBackupError("oauth_token_file_invalid") from None


def bootstrap_oauth(client_config: Path, token_path: Path, folder_id: str,
                    *, opener: Callable[[str], bool] = webbrowser.open,
                    timeout: int = 300) -> dict:
    """Run desktop OAuth + Drive Picker callback on loopback; never prints tokens."""
    _load_google_config(client_config)
    if not folder_id or not all(char.isalnum() or char in "-_" for char in folder_id):
        raise DriveBackupError("drive_folder_invalid")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_config), scopes=[DRIVE_FILE_SCOPE], autogenerate_code_verifier=True,
    )
    received: dict[str, dict] = {}
    state = secrets.token_urlsafe(32)

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/oauth2callback" or query.get("state", [None])[0] != state:
                self.send_error(400)
                return
            received["query"] = {key: values[0] for key, values in query.items() if values}
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Google Drive authorization received. You may close this tab.\n")

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = 1
    flow.redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"
    auth_url, returned_state = flow.authorization_url(
        state=state, access_type="offline", prompt="consent",
        include_granted_scopes="false", trigger_onepick="true",
        allow_folder_selection="true",
    )
    if returned_state != state:
        server.server_close()
        raise DriveBackupError("oauth_state_setup_failed")
    if not opener(auth_url):
        if not sys.stdout.isatty():
            server.server_close()
            raise DriveBackupError("oauth_browser_open_failed")
        print("Automatic browser opening failed. Open this authorization URL manually in your browser:")
        print(auth_url)
    deadline = time.monotonic() + timeout
    try:
        while "query" not in received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    query = received.get("query")
    if not query or query.get("error") or not query.get("code"):
        raise DriveBackupError("oauth_authorization_incomplete")
    if set(query.get("scope", "").split()) != {DRIVE_FILE_SCOPE}:
        raise DriveBackupError("oauth_scope_mismatch")
    if query.get("picked_file_ids", "").split(",") != [folder_id]:
        raise DriveBackupError("oauth_folder_selection_mismatch")
    try:
        flow.fetch_token(code=query["code"])
    except Exception:
        raise DriveBackupError("oauth_exchange_failed") from None
    client = GoogleDriveDestination(folder_id, flow.credentials)
    try:
        client.verify_folder()
    finally:
        client.close()
    _save_credentials(flow.credentials, token_path)
    return {"status": "OAUTH_READY", "folder_id": folder_id,
            "scope": DRIVE_FILE_SCOPE, "token_file": str(token_path)}


class GoogleDriveDestination:
    """Drive v3 transport restricted to one configured folder and app-owned files."""

    def __init__(self, folder_id: str, credentials, *, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, token_path: Path | None = None):
        if not folder_id or not all(char.isalnum() or char in "-_" for char in folder_id):
            raise DriveBackupError("drive_folder_invalid")
        self.folder_id = folder_id
        self.credentials = credentials
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False)
        self._owns_client = client is None
        self.sleep = sleep
        self.token_path = token_path

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {}
        if getattr(self.credentials, "expired", False):
            try:
                from google.auth.transport.requests import Request
                self.credentials.refresh(Request())
                if self.token_path is not None:
                    _save_credentials(self.credentials, self.token_path)
            except Exception:
                raise DriveBackupError("oauth_refresh_failed") from None
        token = getattr(self.credentials, "token", None)
        if not token:
            raise DriveBackupError("oauth_token_unavailable")
        headers["Authorization"] = f"Bearer {token}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _response_error(response: httpx.Response) -> str:
        if response.status_code == 401:
            return "drive_authentication_failed"
        if response.status_code == 403:
            try:
                payload = response.json()
                reasons = {item.get("reason") for item in payload.get("error", {}).get("errors", [])}
            except Exception:
                reasons = set()
            return "drive_rate_limited" if reasons & {"rateLimitExceeded", "userRateLimitExceeded"} else "drive_forbidden"
        if response.status_code == 404:
            return "drive_object_not_found"
        if response.status_code == 429:
            return "drive_rate_limited"
        if response.status_code >= 500:
            return "drive_service_unavailable"
        return "drive_request_failed"

    def _json_request(self, method: str, url: str, **kwargs) -> dict:
        configured_headers = dict(kwargs.pop("headers", {}) or {})
        for attempt in range(MAX_RETRIES + 1):
            headers = dict(configured_headers)
            headers.update(self._headers())
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException:
                if attempt == MAX_RETRIES:
                    raise DriveBackupError("drive_timeout") from None
                self.sleep(min(2**attempt, 8))
                continue
            except httpx.HTTPError:
                raise DriveBackupError("drive_transport_failed") from None
            if (response.status_code in {429, 500, 502, 503, 504}
                    or (response.status_code == 403 and self._response_error(response) == "drive_rate_limited")):
                if attempt == MAX_RETRIES:
                    raise DriveBackupError(self._response_error(response))
                self.sleep(min(2**attempt, 8))
                continue
            if response.is_error:
                raise DriveBackupError(self._response_error(response))
            try:
                return response.json()
            except ValueError:
                raise DriveBackupError("drive_response_invalid") from None
        raise DriveBackupError("drive_request_failed")

    def _request_with_retry(self, method: str, url: str, *, headers: dict,
                            content: bytes | None = None) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.request(method, url, headers=headers, content=content)
            except httpx.TimeoutException:
                if attempt == MAX_RETRIES:
                    raise
                self.sleep(min(2**attempt, 8))
                continue
            except httpx.HTTPError:
                if attempt == MAX_RETRIES:
                    raise
                self.sleep(min(2**attempt, 8))
                continue
            if (response.status_code in {429, 500, 502, 503, 504}
                    or (response.status_code == 403 and self._response_error(response) == "drive_rate_limited")):
                if attempt == MAX_RETRIES:
                    return response
                self.sleep(min(2**attempt, 8))
                continue
            return response
        raise DriveBackupError("drive_transport_failed")

    def _upload_request(self, method: str, url: str, *, headers: dict,
                        content: bytes | None = None) -> httpx.Response:
        """One media request only; caller queries session status after ambiguity."""
        return self.client.request(method, url, headers=headers, content=content)

    def verify_folder(self) -> dict:
        metadata = self._json_request("GET", f"{DRIVE_API}/files/{urllib.parse.quote(self.folder_id, safe='')}",
                                      params={"fields": "id,name,mimeType,capabilities(canAddChildren),trashed"})
        if (metadata.get("id") != self.folder_id or metadata.get("mimeType") != FOLDER_MIME_TYPE
                or metadata.get("trashed") or not metadata.get("capabilities", {}).get("canAddChildren")):
            raise DriveBackupError("drive_folder_not_writable")
        return metadata

    def _list_backup_id(self, backup_id: str) -> list[dict]:
        query = (f"'{self.folder_id}' in parents and trashed = false and "
                 f"appProperties has {{ key='{_APP_MARKER}' and value='{backup_id}' }}")
        result = self._json_request("GET", f"{DRIVE_API}/files", params={
            "q": query, "pageSize": 100,
            "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed)",
        })
        files = list(result.get("files", []))
        while result.get("nextPageToken"):
            result = self._json_request("GET", f"{DRIVE_API}/files", params={
                "q": query, "pageSize": 100, "pageToken": result["nextPageToken"],
                "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed)",
            })
            files.extend(result.get("files", []))
        return files

    def list_backups(self, folder_id: str | None = None) -> list[dict]:
        if folder_id is not None and folder_id != self.folder_id:
            raise DriveBackupError("drive_folder_scope_violation")
        query = (f"'{self.folder_id}' in parents and trashed = false and "
                 "appProperties has { key='marker' and value='v1' }")
        result = self._json_request("GET", f"{DRIVE_API}/files", params={
            "q": query, "pageSize": 100,
            "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed)",
        })
        files = list(result.get("files", []))
        while result.get("nextPageToken"):
            result = self._json_request("GET", f"{DRIVE_API}/files", params={
                "q": query, "pageSize": 100, "pageToken": result["nextPageToken"],
                "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed)",
            })
            files.extend(result.get("files", []))
        return files

    def _verify_remote(self, metadata: dict, expected: dict) -> dict:
        props = metadata.get("appProperties", {})
        if (not metadata.get("id") or metadata.get("trashed")
                or metadata.get("name") != f"{expected['backup_id']}.frcaixinha.tar"
                or metadata.get("mimeType") != "application/x-tar"
                or metadata.get("parents") != [self.folder_id]
                or props.get(_APP_MARKER) != expected["backup_id"]
                or props.get("marker") != "v1"
                or props.get("local_sha256") != expected["sha256"]
                or str(props.get("local_size")) != str(expected["size"])
                or str(metadata.get("size")) != str(expected["size"])
                or metadata.get("md5Checksum") != expected["md5"]):
            raise DriveBackupError("drive_remote_verification_failed")
        return metadata

    def _mark_remote_verified(self, metadata: dict, expected: dict) -> dict:
        properties = dict(metadata.get("appProperties", {}))
        properties["verification_status"] = "VERIFIED"
        result = self._json_request(
            "PATCH", f"{DRIVE_API}/files/{urllib.parse.quote(metadata['id'], safe='')}",
            params={"fields": "id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed"},
            json={"appProperties": properties},
        )
        return self._verify_remote(result, expected)

    def _reconcile(self, expected: dict) -> dict | None:
        matches = self._list_backup_id(expected["backup_id"])
        if len(matches) > 1:
            raise DriveBackupError("drive_duplicate_backup_id")
        if not matches:
            return None
        return self._verify_remote(matches[0], expected)

    def upload_resumable(self, backup_id: str, local_path: Path) -> dict:
        with _backup_upload_lock(local_path.parent, backup_id):
            return self._upload_resumable_locked(backup_id, local_path)["id"]

    def _upload_resumable_locked(self, backup_id: str, local_path: Path) -> dict:
        if local_path.is_symlink() or not local_path.is_file():
            raise DriveBackupError("envelope_invalid")
        manifest = inspect_envelope(local_path)
        if manifest.get("backup_id") != backup_id:
            raise DriveBackupError("envelope_backup_id_mismatch")
        size = local_path.stat().st_size
        if size <= 0 or size > MAX_ENVELOPE_BYTES:
            raise DriveBackupError("envelope_size_limit_exceeded")
        expected = {"backup_id": backup_id, "size": size,
                    "sha256": _sha256(local_path), "md5": _md5(local_path)}
        existing = self._reconcile(expected)
        if existing:
            return self._mark_remote_verified(existing, expected)
        self.verify_folder()
        metadata = {"name": f"{backup_id}.frcaixinha.tar", "mimeType": "application/x-tar",
                    "parents": [self.folder_id], "appProperties": {
                        _APP_MARKER: backup_id, "local_sha256": expected["sha256"],
                        "local_size": str(size), "marker": "v1",
                    }}
        init_url = f"{DRIVE_UPLOAD_API}/files?uploadType=resumable&fields=id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed"
        init = self._request_with_retry("POST", init_url, headers={
            **self._headers(content_type="application/json; charset=UTF-8"),
            "X-Upload-Content-Type": "application/x-tar", "X-Upload-Content-Length": str(size),
        }, content=json.dumps(metadata, separators=(",", ":")).encode())
        if init.status_code not in {200, 201} or not init.headers.get("Location"):
            if init.is_error:
                raise DriveBackupError(self._response_error(init))
            raise DriveBackupError("drive_upload_session_invalid")
        session_url = init.headers["Location"]
        parsed = urllib.parse.urlparse(session_url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "drive.google.com"}:
            raise DriveBackupError("drive_upload_session_host_invalid")
        try:
            result = self._upload_session(session_url, local_path, expected)
        except (DriveBackupError, httpx.HTTPError) as exc:
            if (isinstance(exc, httpx.HTTPError)
                    or (exc.args and exc.args[0] in {"drive_timeout", "drive_transport_failed",
                                                     "drive_ambiguous_upload_result",
                                                     "drive_upload_session_expired"})):
                reconciled = self._reconcile(expected)
                if reconciled:
                    return self._mark_remote_verified(reconciled, expected)
            if isinstance(exc, DriveBackupError):
                raise
            raise DriveBackupError("drive_transport_failed") from None
        self._mark_remote_verified(self._verify_remote(result, expected), expected)
        reconciled = self._reconcile(expected)
        if reconciled is None:
            raise DriveBackupError("drive_remote_reconciliation_failed")
        return self._mark_remote_verified(reconciled, expected)

    def _upload_session(self, session_url: str, path: Path, expected: dict) -> dict:
        size = expected["size"]
        offset = 0
        attempts = 0
        no_progress = 0
        with path.open("rb") as source:
            while offset < size:
                source.seek(offset)
                chunk = source.read(min(CHUNK_BYTES, size - offset))
                end = offset + len(chunk) - 1
                headers = {"Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"}
                try:
                    response = self._upload_request("PUT", session_url, headers=headers, content=chunk)
                except (httpx.TimeoutException, httpx.HTTPError):
                    response = self._request_with_retry("PUT", session_url,
                        headers={"Content-Length": "0", "Content-Range": f"*/{size}"}, content=b"")
                if response.status_code in {500, 502, 503, 504}:
                    response = self._request_with_retry("PUT", session_url,
                        headers={"Content-Length": "0", "Content-Range": f"*/{size}"}, content=b"")
                if response.status_code in {200, 201}:
                    try:
                        return response.json()
                    except ValueError:
                        raise DriveBackupError("drive_ambiguous_upload_result") from None
                if response.status_code == 308:
                    range_header = response.headers.get("Range")
                    new_offset = int(range_header.rsplit("-", 1)[1]) + 1 if range_header else 0
                    no_progress = no_progress + 1 if new_offset <= offset else 0
                    if no_progress > MAX_RETRIES:
                        raise DriveBackupError("drive_upload_no_progress")
                    offset = new_offset
                    if offset > size:
                        raise DriveBackupError("drive_upload_range_invalid")
                    attempts = 0
                    continue
                if response.status_code == 404:
                    raise DriveBackupError("drive_upload_session_expired")
                if (response.status_code in {429, 500, 502, 503, 504}
                        or (response.status_code == 403
                            and self._response_error(response) == "drive_rate_limited")):
                    attempts += 1
                    if attempts <= MAX_RETRIES:
                        status = self._request_with_retry("PUT", session_url,
                            headers={"Content-Length": "0", "Content-Range": f"*/{size}"}, content=b"")
                        if status.status_code == 308:
                            range_header = status.headers.get("Range")
                            new_offset = int(range_header.rsplit("-", 1)[1]) + 1 if range_header else 0
                            no_progress = no_progress + 1 if new_offset <= offset else 0
                            if no_progress > MAX_RETRIES:
                                raise DriveBackupError("drive_upload_no_progress")
                            offset = new_offset
                            continue
                        if status.status_code in {200, 201}:
                            return status.json()
                        self.sleep(min(2**(attempts - 1), 8))
                        continue
                raise DriveBackupError(self._response_error(response))
        raise DriveBackupError("drive_upload_incomplete")

    def describe(self, file_id: str) -> dict:
        item = self._json_request("GET", f"{DRIVE_API}/files/{urllib.parse.quote(file_id, safe='')}",
                                  params={"fields": "id,name,mimeType,size,md5Checksum,parents,appProperties,createdTime,trashed"})
        if item.get("parents") != [self.folder_id] or item.get("trashed"):
            raise DriveBackupError("drive_object_outside_folder")
        return item

    def download(self, file_id: str, target: Path, *, expected_size: int | None = None,
                 expected_sha256: str | None = None) -> dict:
        metadata = self.describe(file_id)
        properties = metadata.get("appProperties", {})
        backup_id = properties.get(_APP_MARKER)
        expected_sha256 = expected_sha256 or properties.get("local_sha256")
        if (properties.get("marker") != "v1" or not isinstance(backup_id, str)
                or not _BACKUP_ID.fullmatch(backup_id)
                or metadata.get("name") != f"{backup_id}.frcaixinha.tar"
                or not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
            raise DriveBackupError("drive_object_not_managed")
        size = int(metadata.get("size", -1))
        if size <= 0 or size > MAX_ENVELOPE_BYTES or (expected_size is not None and size != expected_size):
            raise DriveBackupError("drive_remote_size_mismatch")
        expected_md5 = metadata.get("md5Checksum")
        if not isinstance(expected_md5, str) or not re.fullmatch(r"[0-9a-f]{32}", expected_md5):
            raise DriveBackupError("drive_remote_checksum_missing")
        if target.exists() or target.is_symlink():
            raise DriveBackupError("download_target_exists")
        _private_directory(target.parent, create=True)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.partial")
        headers = self._headers()
        url = f"{DRIVE_API}/files/{urllib.parse.quote(file_id, safe='')}?alt=media"
        digest = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        total = 0
        try:
            with self.client.stream("GET", url, headers=headers) as response:
                if response.is_error:
                    raise DriveBackupError(self._response_error(response))
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as sink:
                    for block in response.iter_bytes(1024 * 1024):
                        total += len(block)
                        if total > size or total > MAX_ENVELOPE_BYTES:
                            raise DriveBackupError("drive_download_size_mismatch")
                        digest.update(block)
                        md5.update(block)
                        sink.write(block)
                    sink.flush()
                    os.fsync(sink.fileno())
            if total != size or md5.hexdigest() != expected_md5:
                raise DriveBackupError("drive_download_checksum_mismatch")
            sha = digest.hexdigest()
            if expected_sha256 and sha != expected_sha256:
                raise DriveBackupError("drive_download_checksum_mismatch")
            os.replace(temporary, target)
            target.chmod(0o600)
            return {"file_id": file_id, "size": total, "sha256": sha, "md5": md5.hexdigest(),
                    "verification_status": "VERIFIED"}
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise

    def upload_package(self, package_dir: Path, work_dir: Path, receipt_path: Path) -> dict:
        manifest = _read_manifest(package_dir)
        _private_directory(work_dir, create=True)
        with _backup_upload_lock(work_dir, manifest["backup_id"]):
            return self._upload_package_locked(package_dir, work_dir, receipt_path, manifest)

    def _upload_package_locked(self, package_dir: Path, work_dir: Path,
                               receipt_path: Path, manifest: dict) -> dict:
        envelope_path = work_dir / f"{manifest['backup_id']}.frcaixinha.tar"
        if envelope_path.exists():
            if envelope_path.is_symlink() or not envelope_path.is_file():
                raise DriveBackupError("envelope_invalid")
            temp_envelope = work_dir / f".{manifest['backup_id']}.{secrets.token_hex(8)}.tar"
            try:
                candidate = create_envelope(package_dir, temp_envelope)
                if _sha256(envelope_path) != candidate["sha256"] or envelope_path.stat().st_size != candidate["size"]:
                    raise DriveBackupError("existing_envelope_mismatch")
                envelope = {**candidate, "filename": envelope_path.name}
            finally:
                temp_envelope.unlink(missing_ok=True)
        else:
            envelope = create_envelope(package_dir, envelope_path)
        receipt = {"receipt_version": 1, "provider": "google_drive", "backup_id": manifest["backup_id"],
                   "folder_id": self.folder_id, "local_envelope": envelope,
                   "remote_file_id": None, "remote_name": envelope_path.name, "remote_size": None,
                   "remote_md5": None, "local_sha256": envelope["sha256"], "uploaded_at_utc": None,
                   "verification_status": "UPLOADING", "status": "UPLOADING"}
        _atomic_json(receipt_path, receipt)
        try:
            remote = self._upload_resumable_locked(manifest["backup_id"], envelope_path)
            self._verify_remote(remote, envelope)
            receipt.update({"remote_file_id": remote["id"], "remote_name": remote["name"],
                            "remote_size": int(remote["size"]), "remote_md5": remote["md5Checksum"],
                            "uploaded_at_utc": _utc_now(), "verification_status": "VERIFIED",
                            "status": "COMPLETE_OFF_VM"})
            _atomic_json(receipt_path, receipt)
            return receipt
        except Exception:
            receipt.update({"verification_status": "FAILED", "status": "FAILED_OFF_VM"})
            _atomic_json(receipt_path, receipt)
            raise

    def retention_candidates(self, *, before: datetime) -> dict:
        """Return app-managed old objects for review; this method never deletes."""
        if before.tzinfo is None:
            raise DriveBackupError("retention_cutoff_must_be_aware")
        items = self.list_backups(self.folder_id)
        known = [item for item in items if item.get("appProperties", {}).get("marker") == "v1"
                 and item.get("createdTime")]
        cutoff = before.astimezone(timezone.utc)
        verified = [item for item in known
                    if item.get("appProperties", {}).get("verification_status") == "VERIFIED"
                    and item.get("size") and item.get("md5Checksum")
                    and item.get("appProperties", {}).get("local_sha256")]
        verified.sort(key=lambda item: item["createdTime"])
        candidates = [item for item in verified
                      if datetime.fromisoformat(item["createdTime"].replace("Z", "+00:00")) < cutoff]
        candidates.sort(key=lambda item: item["createdTime"])
        if verified and verified[0] in candidates:
            candidates.remove(verified[0])
        return {"mode": "DRY_RUN", "candidate_file_ids": [item["id"] for item in candidates],
                "delete_performed": False}


def _configured_client() -> GoogleDriveDestination:
    folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    token_path = Path(os.environ.get("GOOGLE_DRIVE_OAUTH_TOKEN_FILE", ""))
    if not token_path.is_absolute():
        raise DriveBackupError("oauth_token_file_config_invalid")
    return GoogleDriveDestination(folder, load_credentials(token_path), token_path=token_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Portable encrypted off-VM backup transport")
    sub = parser.add_subparsers(dest="operation", required=True)
    bootstrap = sub.add_parser("oauth-bootstrap")
    bootstrap.add_argument("--client-config", type=Path, required=True)
    bootstrap.add_argument("--token-file", type=Path, required=True)
    bootstrap.add_argument("--folder-id", default=os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""))
    pack = sub.add_parser("pack")
    pack.add_argument("--backup-directory", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    upload = sub.add_parser("upload")
    upload.add_argument("--backup-directory", type=Path, required=True)
    upload.add_argument("--work-dir", type=Path, required=True)
    upload.add_argument("--receipt", type=Path, required=True)
    download = sub.add_parser("download")
    download.add_argument("--file-id", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--extract-to", type=Path, required=True)
    download.add_argument("--expected-size", type=int)
    download.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        if args.operation == "oauth-bootstrap":
            result = bootstrap_oauth(args.client_config, args.token_file, args.folder_id)
        elif args.operation == "pack":
            result = create_envelope(args.backup_directory, args.output)
        else:
            client = _configured_client()
            try:
                if args.operation == "upload":
                    result = client.upload_package(args.backup_directory, args.work_dir, args.receipt)
                else:
                    result = client.download(args.file_id, args.output, expected_size=args.expected_size,
                                             expected_sha256=args.expected_sha256)
                    result["extracted_directory"] = str(extract_envelope(
                        args.output, args.extract_to, expected_sha256=result["sha256"]))
            finally:
                client.close()
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        code = exc.args[0] if isinstance(exc, (DriveBackupError, protection.BackupError)) and exc.args else "backup_transport_failed"
        print(json.dumps({"status": "FAILED", "error_code": code}, sort_keys=True))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
