# OCI data protection: Phase B Google Drive transport

This phase sends the same encrypted, verified Phase A backup directory to
Google Drive. It does not create a Google-specific restore format. The portable
uncompressed tar envelope contains only the Phase A manifest and files that
were already encrypted with age. It can be copied to offline media and restored
without Google software or credentials.

## Approved authentication boundary

The approved account is the Administrator Master’s personal Google account.
The application is an installed desktop OAuth client and requests only
https://www.googleapis.com/auth/drive.file. It asks for offline access and
uses the desktop Google Picker authorization redirect to select the approved
folder. It rejects any other returned scope or a selection that does not match
GOOGLE_DRIVE_FOLDER_ID.

The drive.file scope is not an OAuth ACL limited to one folder ID. The client
therefore enforces the configured folder in every API query and validates the
parent folder before accepting upload, list, or download results. It does not
create permissions, move files, or make anything public.

Before bootstrap, the Administrator Master must:

1. Enable Google Drive API in the chosen Google Cloud project.
2. Configure the OAuth consent screen for the personal account and publish the
   app for persistent unattended refresh-token use. A personal Google account
   cannot use an organization-only internal consent audience. Google documents
   that external apps left in Testing may receive refresh tokens that expire
   after seven days for scopes such as drive.file.
3. Create an OAuth client of type Desktop app and download its client
   configuration JSON. Do not commit it. Copy it to a private host path and
   set its mode to 0600.
4. Configure the folder ID as
   1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU. During the one-time browser flow,
   select this exact folder in the Picker. Bootstrap verifies the returned ID
   and that Drive reports it as a writable folder.
5. Run the bootstrap manually on a machine with a browser:

   cd backend
   umask 077
   python -m app.backup_transport oauth-bootstrap --client-config /secure/path/google-oauth-client.json --token-file /secure/path/google-drive-token.json --folder-id 1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU

The client configuration and resulting token file remain outside Git. Token
refresh is persisted atomically with mode 0600. The program never prints the
authorization code, access token, refresh token, or client secret. Revocation
is performed by removing the app’s access in the Google Account security
settings; it must also be treated as a backup destination outage until a new
consent is performed. Transfer the generated token file to the VM over an
approved secure channel, place it outside the repository, and keep it at mode
0600 in a mode-0700 directory. The token file contains OAuth credential
material and must have a protected off-VM recovery copy; it must not be stored
inside the backup folder.

Google’s Picker desktop/mobile flow requires drive.file, prompt=consent, and
trigger_onepick=true; this implementation also requests folder selection. The
native installed-app flow uses PKCE and a loopback-only callback. Official
references:

- https://developers.google.com/workspace/drive/picker/guides/desktop-mobile-picker
- https://developers.google.com/identity/protocols/oauth2/native-app
- https://developers.google.com/workspace/drive/api/guides/manage-uploads
- https://developers.google.com/workspace/drive/api/guides/handle-errors
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files

## Package, upload, and receipt

COMPLETE_LOCAL remains the Phase A state. It does not mean an off-VM copy
exists. Upload creates a deterministic uncompressed tar envelope with
normalized ownership, permissions, and timestamps. The manifest remains
byte-for-byte unchanged; the SHA-256 and size of the envelope are stored in a
separate private receipt. The Drive object is marked COMPLETE_OFF_VM in that
receipt only after its parent, app properties, size, MD5 checksum, and local
SHA-256 identity have been reconciled. Drive exposes MD5 for binary files; the
local SHA-256 remains the stronger package identity and is also stored as
private app metadata.

The stable logical identity is backup_id, carried in Drive appProperties. The
uploader lists only inside the configured folder and reconciles before and
after transfer. A local advisory lock serializes same-host retries. Drive
does not provide a unique constraint on appProperties; if independent hosts
race, the client detects multiple matching objects and fails for operator
review rather than claiming success. Incomplete resumable sessions are never
marked complete; an expired session can be restarted after checking for a
completed object.

The Google Drive API uses resumable upload sessions for encrypted tar files,
and the client applies bounded retries to transient timeouts, rate limits, and
server errors. API errors are reduced to stable codes in CLI output. HTTP 401,
403 permission errors, and 404 are not treated as transient; only the
documented rate-limit reasons within 403 are retried.

The local receipt is an operational sidecar, not part of the Phase A manifest.
It records provider, backup ID, folder ID, remote file ID, remote name/size/MD5,
local envelope SHA-256, upload time, and verification state. A failed remote
upload leaves the valid local COMPLETE_LOCAL set untouched. Retention is
dry-run only; there is no delete operation.

## Download and restore

Download requires a file ID. The client confirms that it is an app-managed
binary object whose parent is the configured folder, streams it to a private
temporary file, and checks exact size, Drive MD5, and local SHA-256 before
renaming it to the requested output. extract_envelope validates every tar
member, refuses links, devices, duplicate or unexpected paths, bounds sizes,
and writes only into a new private directory. It then rechecks the Phase A
manifest and all encrypted file SHA-256 values. Run the existing isolated
restore harness separately against a clean loopback PostgreSQL 16 database;
this transport does not start the API or worker or connect to production.

## Commands

Pack:
python -m app.backup_transport pack --backup-directory /private/staging/BACKUP_ID --output /private/transport/BACKUP_ID.frCAIXINHA.tar

Upload:
python -m app.backup_transport upload --backup-directory /private/staging/BACKUP_ID --work-dir /private/drive-work --receipt /private/drive-work/BACKUP_ID.remote-receipt.json

Download:
python -m app.backup_transport download --file-id DRIVE_FILE_ID --output /private/restore/BACKUP_ID.tar --extract-to /private/restore/BACKUP_ID

Configure GOOGLE_DRIVE_FOLDER_ID and an absolute
GOOGLE_DRIVE_OAUTH_TOKEN_FILE before upload/download. Pack and extraction
remain provider-independent for future offline media. The private age identity
is not included in the envelope.

## Provider limitations and human follow-up

- No live OAuth client, consent, Google upload, or Google download was used in
  CI or during implementation.
- The folder ID is configuration, not authorization. drive.file is not an
  exclusive folder ACL; the app’s one-folder checks are an operational guard.
- If Google OAuth is left in external Testing, its refresh token can expire
  after seven days for this scope. Publishing and account policy must be
  completed by the Administrator Master before unattended production use.
- Drive API md5Checksum is used only as the provider’s transport checksum.
  Local SHA-256 and the Phase A per-file checks remain authoritative.
- The package and receipt have a default 100 GiB envelope bound. Production
  disk sizing and an approved retention/frequency policy remain separate
  decisions.
- Google authorization requires a browser during one-time bootstrap. The
  OAuth token must be backed up securely outside the VM or reauthorization
  becomes necessary after VM loss; it must never be placed beside Drive backup
  objects.
- Physical media has not been accessed. It uses the same portable tar file.
