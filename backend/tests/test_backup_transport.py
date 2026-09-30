"""Deterministic tests for the portable envelope and mocked Drive transport."""

import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import types
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app import backup_transport as transport
from app import data_protection as protection


def _package(root: Path) -> Path:
    directory = root / ("20260930T000000Z-" + "a" * 32)
    directory.mkdir(mode=0o700)
    (directory / "evidence").mkdir(mode=0o700)
    archive = directory / "postgres.dump.age"
    archive.write_bytes(b"age encrypted archive")
    evidence = directory / "evidence" / "workflow-1.age"
    evidence.write_bytes(b"age encrypted evidence")
    manifest = {
        "manifest_version": 1,
        "backup_id": directory.name,
        "status": "COMPLETE_LOCAL",
        "off_vm_status": "NOT_UPLOADED",
        "archive_format": "custom",
        "archive": protection._metadata(archive),
        "evidence_file_count": 1,
        "evidence": [{"encrypted": protection._metadata(evidence), "path": "workflow/1"}],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    (directory / "manifest.json").chmod(0o600)
    return directory


def test_envelope_is_deterministic_private_and_locally_restorable(tmp_path):
    package = _package(tmp_path)
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    first, second = output / "one.tar", output / "two.tar"
    first_meta = transport.create_envelope(package, first)
    second_meta = transport.create_envelope(package, second)
    assert first.read_bytes() == second.read_bytes()
    assert first_meta["sha256"] == second_meta["sha256"]
    assert first.stat().st_mode & 0o077 == 0
    restored = output / "restored"
    transport.extract_envelope(first, restored, expected_sha256=first_meta["sha256"])
    assert (restored / "postgres.dump.age").read_bytes() == b"age encrypted archive"
    assert (restored / "evidence" / "workflow-1.age").read_bytes() == b"age encrypted evidence"
    assert (restored / "manifest.json").read_bytes() == (package / "manifest.json").read_bytes()
    assert restored.stat().st_mode & 0o077 == 0
    assert (restored / "manifest.json").stat().st_mode & 0o077 == 0


def test_envelope_rejects_source_symlinks_unexpected_and_corrupt_files(tmp_path):
    package = _package(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"unexpected")
    (package / "extra").symlink_to(outside)
    with pytest.raises(transport.DriveBackupError, match="backup_symlink_rejected"):
        transport.create_envelope(package, tmp_path / "out.tar")
    (package / "extra").unlink()
    (package / "extra").write_bytes(b"unexpected")
    with pytest.raises(transport.DriveBackupError, match="backup_members_unexpected"):
        transport.create_envelope(package, tmp_path / "out.tar")


def test_envelope_never_accepts_age_identity_material(tmp_path):
    package = _package(tmp_path)
    (package / "operator.agekey").write_text("AGE-SECRET-KEY-not-for-package")
    (package / "operator.agekey").chmod(0o600)
    with pytest.raises(transport.DriveBackupError, match="backup_members_unexpected"):
        transport.create_envelope(package, tmp_path / "out.tar")


def test_envelope_rejects_path_traversal_before_extraction(tmp_path):
    package = _package(tmp_path)
    output = tmp_path / "transport.tar"
    manifest = (package / "manifest.json").read_bytes()
    with tarfile.open(output, "w") as archive:
        for name, data in (("manifest.json", manifest), ("../escape", b"bad")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, __import__("io").BytesIO(data))
    destination = tmp_path / "extracted"
    with pytest.raises(transport.DriveBackupError, match="envelope_member_invalid"):
        transport.extract_envelope(output, destination)
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_envelope_rejects_truncation_checksum_and_extra_members(tmp_path):
    package = _package(tmp_path)
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    envelope = output / "backup.tar"
    metadata = transport.create_envelope(package, envelope)
    corrupted = output / "corrupted.tar"
    corrupted.write_bytes(envelope.read_bytes()[:-7])
    with pytest.raises(transport.DriveBackupError):
        transport.extract_envelope(corrupted, output / "bad", expected_sha256=metadata["sha256"])
    extra = output / "extra.tar"
    with tarfile.open(extra, "w") as archive:
        for name in ("manifest.json", "postgres.dump.age", "evidence/workflow-1.age", "unexpected"):
            data = (package / name).read_bytes() if name != "unexpected" else b"extra"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, __import__("io").BytesIO(data))
    with pytest.raises(transport.DriveBackupError, match="envelope_members_unexpected"):
        transport.extract_envelope(extra, output / "bad-extra")


class _Credentials:
    token = "test-token"
    expired = False


def _remote_metadata(path: Path, folder_id: str, backup_id: str) -> dict:
    return {
        "id": "drive-file-1",
        "name": f"{backup_id}.frcaixinha.tar",
        "mimeType": "application/x-tar",
        "size": str(path.stat().st_size),
        "md5Checksum": transport._md5(path),
        "parents": [folder_id],
        "appProperties": {
            "frcaixinha_backup_v1": backup_id,
            "local_sha256": transport._sha256(path),
            "local_size": str(path.stat().st_size),
            "marker": "v1",
        },
        "createdTime": "2026-09-30T01:00:00.000Z",
        "trashed": False,
    }


def _mock_drive(path: Path, folder_id: str, backup_id: str, *, ambiguous=False,
                initial_503=False):
    remote = _remote_metadata(path, folder_id, backup_id)
    state = {"chunks": 0, "deleted": False, "requests": []}

    def handler(request: httpx.Request):
        state["requests"].append(request)
        if request.method == "GET" and request.url.path.endswith("/files"):
            files = [remote] if state["chunks"] > 0 else []
            return httpx.Response(200, json={"files": files})
        if request.method == "GET" and request.url.path.endswith(f"/files/{folder_id}"):
            return httpx.Response(200, json={"id": folder_id, "mimeType": transport.FOLDER_MIME_TYPE,
                                             "capabilities": {"canAddChildren": True}, "trashed": False})
        if request.method == "POST" and request.url.path.endswith("/files"):
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/resumable/test"})
        if request.method == "PUT" and request.url.path.endswith("/resumable/test"):
            state["chunks"] += 1
            if initial_503 and state["chunks"] == 1:
                return httpx.Response(503)
            if initial_503 and request.headers.get("Content-Range", "").startswith("*/"):
                return httpx.Response(308, headers={"Range": "bytes=0-4"})
            if state["chunks"] == 1 and request.headers.get("Content-Range", "").startswith("bytes 0-"):
                return httpx.Response(308, headers={"Range": "bytes=0-4"})
            if ambiguous:
                return httpx.Response(201, content=b"")
            return httpx.Response(201, json=remote)
        if request.method == "PATCH" and request.url.path.endswith("/files/drive-file-1"):
            payload = json.loads(request.content)
            remote["appProperties"] = payload["appProperties"]
            return httpx.Response(200, json=remote)
        if (request.method == "GET" and request.url.path.endswith("/files/drive-file-1")
                and not request.url.params.get("alt")):
            return httpx.Response(200, json=remote)
        if request.method == "GET" and request.url.params.get("alt") == "media":
            return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(500, json={"error": "unexpected test request"})

    return httpx.Client(transport=httpx.MockTransport(handler)), state, remote


def test_google_upload_resumable_reconciles_and_marks_receipt_verified(tmp_path):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope_path = work / "upload.tar"
    envelope_info = transport.create_envelope(package, envelope_path)
    backup_id = package.name
    folder_id = "approved-folder"
    client, state, remote = _mock_drive(envelope_path, folder_id, backup_id)
    destination = transport.GoogleDriveDestination(folder_id, _Credentials(), client=client, sleep=lambda _: None)
    result = destination.upload_resumable(backup_id, envelope_path)
    assert result == "drive-file-1"
    assert state["chunks"] == 2
    assert remote["appProperties"]["verification_status"] == "VERIFIED"
    assert envelope_info["sha256"] == remote["appProperties"]["local_sha256"]
    assert destination.upload_resumable(backup_id, envelope_path) == "drive-file-1"
    assert sum(request.method == "POST" for request in state["requests"]) == 1
    assert all("/permissions" not in request.url.path for request in state["requests"])
    assert all(request.headers.get("Authorization") == "Bearer test-token"
               for request in state["requests"] if request.url.path.endswith("/files"))


def test_ambiguous_upload_response_reconciles_before_retrying(tmp_path):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope = work / "upload.tar"
    transport.create_envelope(package, envelope)
    client, state, remote = _mock_drive(envelope, "approved-folder", package.name, ambiguous=True)
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials(), client=client, sleep=lambda _: None)
    assert destination.upload_resumable(package.name, envelope) == "drive-file-1"
    assert remote["appProperties"]["verification_status"] == "VERIFIED"
    assert sum(request.method == "POST" for request in state["requests"]) == 1


def test_resumable_server_failure_queries_session_before_continuing(tmp_path):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope = work / "upload.tar"
    transport.create_envelope(package, envelope)
    client, state, _remote = _mock_drive(
        envelope, "approved-folder", package.name, initial_503=True
    )
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials(),
                                                    client=client, sleep=lambda _: None)
    assert destination.upload_resumable(package.name, envelope) == "drive-file-1"
    assert any(request.headers.get("Content-Range") == f"*/{envelope.stat().st_size}"
               for request in state["requests"])
    assert any(request.headers.get("Content-Range", "").startswith("bytes 5-")
               for request in state["requests"])


def test_json_request_retries_drive_rate_limit_with_bounded_delay():
    attempts = []
    delays = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(403, json={"error": {"errors": [
                {"reason": "userRateLimitExceeded"}
            ]}})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials(),
                                                    client=client, sleep=delays.append)
    assert destination._json_request("GET", "https://www.googleapis.com/drive/v3/files") == {"ok": True}
    assert len(attempts) == 2 and delays == [1]
    client.close()


def test_google_download_checks_drive_md5_and_local_sha_before_extract(tmp_path):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope = work / "backup.tar"
    info = transport.create_envelope(package, envelope)
    remote = _remote_metadata(envelope, "approved-folder", package.name)

    def handler(request):
        if (request.method == "GET" and request.url.path.endswith("/files/drive-file-1")
                and not request.url.params.get("alt")):
            return httpx.Response(200, json=remote)
        if request.method == "GET" and request.url.params.get("alt") == "media":
            return httpx.Response(200, content=envelope.read_bytes())
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials(), client=client)
    downloaded = work / "download.tar"
    result = destination.download("drive-file-1", downloaded, expected_sha256=info["sha256"])
    assert result["verification_status"] == "VERIFIED"
    restored = work / "restored"
    transport.extract_envelope(downloaded, restored, expected_sha256=result["sha256"])
    assert (restored / "manifest.json").is_file()

    remote["md5Checksum"] = "0" * 32
    bad = work / "bad.tar"
    with pytest.raises(transport.DriveBackupError, match="drive_download_checksum_mismatch"):
        destination.download("drive-file-1", bad)
    assert not bad.exists()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "drive_authentication_failed"), (403, "drive_forbidden"), (404, "drive_object_not_found"),
     (429, "drive_rate_limited"), (503, "drive_service_unavailable")],
)
def test_drive_error_classification_is_stable_and_non_sensitive(status, expected):
    response = httpx.Response(status, json={"error": {"message": "secret-token", "errors": []}})
    assert transport.GoogleDriveDestination._response_error(response) == expected


def test_rate_limited_upload_retries_and_wrong_parent_is_rejected(tmp_path):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope = work / "backup.tar"
    transport.create_envelope(package, envelope)
    remote = _remote_metadata(envelope, "wrong-folder", package.name)
    with pytest.raises(transport.DriveBackupError, match="drive_remote_verification_failed"):
        transport.GoogleDriveDestination("approved-folder", _Credentials())._verify_remote(
            remote, {"backup_id": package.name, "size": envelope.stat().st_size,
                     "sha256": transport._sha256(envelope), "md5": transport._md5(envelope)})


def test_duplicate_backup_id_and_scope_violation_fail_closed(tmp_path, monkeypatch):
    package = _package(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    envelope = work / "backup.tar"
    transport.create_envelope(package, envelope)
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials())
    expected = {"backup_id": package.name, "size": envelope.stat().st_size,
                "sha256": transport._sha256(envelope), "md5": transport._md5(envelope)}
    monkeypatch.setattr(destination, "_list_backup_id", lambda _backup_id: [{}, {}])
    with pytest.raises(transport.DriveBackupError, match="drive_duplicate_backup_id"):
        destination._reconcile(expected)
    with pytest.raises(transport.DriveBackupError, match="drive_folder_scope_violation"):
        destination.list_backups("some-other-folder")
    destination.close()


def test_retention_is_dry_run_and_preserves_last_verified_backup(tmp_path, monkeypatch):
    destination = transport.GoogleDriveDestination("approved-folder", _Credentials())
    items = [
        {"id": "oldest", "createdTime": "2025-01-01T00:00:00Z",
         "appProperties": {"marker": "v1", "verification_status": "VERIFIED", "local_sha256": "a"},
         "size": "10", "md5Checksum": "x"},
        {"id": "newer", "createdTime": "2025-02-01T00:00:00Z",
         "appProperties": {"marker": "v1", "verification_status": "VERIFIED", "local_sha256": "b"},
         "size": "10", "md5Checksum": "y"},
        {"id": "unknown", "createdTime": "2024-01-01T00:00:00Z",
         "appProperties": {"marker": "v1"}, "size": "10", "md5Checksum": "z"},
    ]
    monkeypatch.setattr(destination, "list_backups", lambda _folder: items)
    result = destination.retention_candidates(before=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result == {"mode": "DRY_RUN", "candidate_file_ids": ["newer"], "delete_performed": False}
    destination.close()


def test_oauth_config_is_desktop_only_private_and_scope_save_rejects_broad(tmp_path):
    config = tmp_path / "oauth.json"
    config.write_text(json.dumps({"installed": {"client_id": "test", "client_secret": "not-real"}}))
    config.chmod(0o600)
    assert transport._load_google_config(config)["installed"]["client_id"] == "test"
    config.chmod(0o644)
    with pytest.raises(transport.DriveBackupError, match="oauth_client_file_invalid"):
        transport._load_google_config(config)

    class Credentials:
        scopes = [transport.DRIVE_FILE_SCOPE, "https://www.googleapis.com/auth/drive"]
        granted_scopes = scopes

    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    with pytest.raises(transport.DriveBackupError, match="oauth_scope_mismatch"):
        transport._save_credentials(Credentials(), private_dir / "token.json")


@pytest.mark.parametrize(
    ("browser_opens", "interactive_terminal", "callback_arrives"),
    [
        pytest.param(True, True, True, id="automatic-browser"),
        pytest.param(False, True, True, id="manual-browser-fallback"),
        pytest.param(False, True, False, id="manual-browser-timeout"),
        pytest.param(False, False, False, id="noninteractive-no-url"),
    ],
)
def test_oauth_bootstrap_uses_pkce_drive_file_offline_picker_and_private_token(
        tmp_path, monkeypatch, browser_opens, interactive_terminal, callback_arrives):
    config = tmp_path / "google-oauth-client.json"
    config.write_text(json.dumps({"installed": {"client_id": "client-id",
                                                "client_secret": "not-a-real-secret"}}))
    config.chmod(0o600)
    token = tmp_path / "private" / "google-drive-token.json"
    token.parent.mkdir(mode=0o700)
    token.parent.chmod(0o700)
    state = {}

    class TerminalOutput(io.StringIO):
        def isatty(self):
            return interactive_terminal

    terminal = TerminalOutput()
    monkeypatch.setattr(transport.sys, "stdout", terminal)

    class FakeCredentials:
        scopes = [transport.DRIVE_FILE_SCOPE]
        granted_scopes = [transport.DRIVE_FILE_SCOPE]

        def to_json(self):
            return json.dumps({"scopes": self.scopes, "token": "fake-access",
                               "refresh_token": "fake-refresh"})

    class FakeFlow:
        credentials = FakeCredentials()

        @classmethod
        def from_client_secrets_file(cls, filename, *, scopes, autogenerate_code_verifier):
            state["config"] = filename
            state["scopes"] = scopes
            state["pkce"] = autogenerate_code_verifier
            state["flow"] = cls()
            return state["flow"]

        def authorization_url(self, **kwargs):
            state["auth_args"] = kwargs
            state["state"] = kwargs["state"]
            auth_url = "https://accounts.google.com/o/oauth2/v2/auth?scope=" + urllib.parse.quote(
                transport.DRIVE_FILE_SCOPE)
            state["auth_url"] = auth_url
            return auth_url, kwargs["state"]

        def fetch_token(self, *, code):
            assert not token.exists()
            state["code"] = code

    google_oauth = types.ModuleType("google_auth_oauthlib")
    google_oauth.__path__ = []
    flow_module = types.ModuleType("google_auth_oauthlib.flow")
    flow_module.InstalledAppFlow = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", google_oauth)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    def verify_folder(client):
        assert not token.exists()
        return {"id": client.folder_id}

    monkeypatch.setattr(transport.GoogleDriveDestination, "verify_folder", verify_folder)

    def open_picker(url):
        args = state["auth_args"]
        assert args["access_type"] == "offline"
        assert args["prompt"] == "consent"
        assert args["trigger_onepick"] == "true"
        assert args["allow_folder_selection"] == "true"
        assert args["include_granted_scopes"] == "false"
        assert state["scopes"] == [transport.DRIVE_FILE_SCOPE]
        assert state["pkce"] is True
        callback = state["flow"]

        def respond():
            query = urllib.parse.urlencode({
                "state": state["state"], "code": "synthetic-code",
                "scope": transport.DRIVE_FILE_SCOPE, "picked_file_ids": "approved-folder",
            })
            urllib.request.urlopen(callback.redirect_uri + "?" + query, timeout=3).read()

        if callback_arrives:
            thread = threading.Thread(target=respond)
            thread.start()
            state["thread"] = thread
        return browser_opens

    if callback_arrives:
        result = transport.bootstrap_oauth(config, token, "approved-folder",
                                           opener=open_picker, timeout=4)
        state["thread"].join(timeout=4)
        assert state["code"] == "synthetic-code"
        assert result["status"] == "OAUTH_READY"
        assert token.stat().st_mode & 0o077 == 0
        assert "fake-access" not in json.dumps(result)
        assert "fake-refresh" not in json.dumps(result)
        assert "synthetic-code" not in token.read_text(encoding="utf-8")
    else:
        error = (
            "oauth_browser_open_failed"
            if not interactive_terminal
            else "oauth_authorization_incomplete"
        )
        with pytest.raises(transport.DriveBackupError, match=error):
            transport.bootstrap_oauth(config, token, "approved-folder",
                                      opener=open_picker, timeout=0.05)
        assert not token.exists()

    output = terminal.getvalue()
    if not browser_opens and interactive_terminal:
        assert "Open this authorization URL manually" in output
        assert state["auth_url"] in output
    else:
        assert state.get("auth_url", "") not in output
    for secret_or_code in ("not-a-real-secret", "fake-access", "fake-refresh", "synthetic-code"):
        assert secret_or_code not in output


def test_refresh_persists_rotated_token_privately(tmp_path, monkeypatch):
    package = types.ModuleType("google")
    package.__path__ = []
    auth = types.ModuleType("google.auth")
    auth.__path__ = []
    transport_pkg = types.ModuleType("google.auth.transport")
    transport_pkg.__path__ = []
    requests = types.ModuleType("google.auth.transport.requests")

    class Request:
        pass

    requests.Request = Request
    monkeypatch.setitem(sys.modules, "google", package)
    monkeypatch.setitem(sys.modules, "google.auth", auth)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_pkg)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests)
    token_path = tmp_path / "private" / "token.json"
    token_path.parent.mkdir(mode=0o700)
    token_path.parent.chmod(0o700)

    class ExpiredCredentials:
        token = "old"
        expired = True
        scopes = [transport.DRIVE_FILE_SCOPE]
        granted_scopes = scopes

        def refresh(self, request):
            assert isinstance(request, Request)
            self.token = "rotated"
            self.expired = False

        def to_json(self):
            return json.dumps({"scopes": self.scopes, "token": self.token})

    credentials = ExpiredCredentials()
    client = transport.GoogleDriveDestination("approved-folder", credentials,
                                              client=httpx.Client(), token_path=token_path)
    assert client._headers()["Authorization"] == "Bearer rotated"
    assert json.loads(token_path.read_text())["token"] == "rotated"
    assert token_path.stat().st_mode & 0o077 == 0
    client.close()


def test_receipt_atomic_private_and_contains_no_oauth_material(tmp_path):
    receipt = tmp_path / "private" / "receipt.json"
    transport._atomic_json(receipt, {"status": "FAILED_OFF_VM", "error_code": "drive_forbidden"})
    raw = receipt.read_text()
    assert receipt.stat().st_mode & 0o077 == 0
    assert "access_token" not in raw and "refresh_token" not in raw and "client_secret" not in raw
    assert json.loads(raw)["status"] == "FAILED_OFF_VM"
