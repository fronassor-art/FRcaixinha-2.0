# OCI data protection: Phase A core

This phase provides an encrypted **local** PostgreSQL and evidence backup set,
plus an isolated restore test. It is not a production backup schedule, Google
Drive integration, or approval to restore a production database. The old
`scripts/backup_postgres.sh` and generic systemd timer do not provide this
contract and must not be treated as production data protection.

## Destination boundary

`BACKUP_OFF_VM_PROVIDER=google_drive` and
`GOOGLE_DRIVE_FOLDER_ID=1dXpu50BErONnQRPBdQ5ND1EUVAyRGsqU` identify the
approved future destination. A folder ID is not a credential. This package
implements only the `OffVmDestination` interface; it performs **no Google
authentication, upload, download or retention**. Phase B transport and OAuth
bootstrap are described in the Phase B guide. No live authorization or
provider request is implied by the Phase A local capabilities.
[See the Phase B Google Drive transport guide](oci-data-protection-phase-b.md)
for the approved provider boundary and operational setup.

## Local package and credentials

Run `python -m app.data_protection backup --staging-root PATH
--evidence-root PATH` from `backend` only after supplying:

- `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`, and a mode-0600 `PGPASSFILE`;
  `PGPASSWORD` is rejected. The database password is never passed in a command
  argument or logged by this module.
- `BACKUP_OFF_VM_PROVIDER=google_drive`, `GOOGLE_DRIVE_FOLDER_ID`,
  `BACKUP_APPLICATION_COMMIT` (full 40-character SHA), and the **public**
  `BACKUP_AGE_RECIPIENT`. The private age identity is not needed on the backup
  host. PostgreSQL 16 `pg_dump`/`pg_restore` and age v1.2.1 are the tested
  tool versions in CI.
- A pre-existing mode-0700 staging directory and the POSIX evidence root. The
  application must have already migrated to `0106_event_financial_date_f2e1`.

The tool exports one PostgreSQL MVCC snapshot, inventories evidence references
under that snapshot and passes it to `pg_dump -Fc`. It streams the custom dump
and each referenced evidence file through age. Each evidence stream is hashed
while being encrypted and must match the size and SHA-256 in the database.
Only then is the manifest atomically marked `COMPLETE_LOCAL`. Any failure marks
it `FAILED`; a failed or incomplete directory is not a valid backup. There is
no `COMPLETE` off-VM state in Phase A.

`manifest.json` version 1 contains the backup ID, UTC timestamps, commit,
Alembic revision, PostgreSQL/tool versions, custom archive metadata, encrypted
archive/evidence sizes and SHA-256 hashes, non-secret age recipient fingerprint,
Google Drive provider/folder metadata, `remote_file_id=null`, and
`off_vm_status=NOT_UPLOADED`. It contains no URL, token, private key or
unnecessary personal data. Keep the manifest and encrypted files together.

**Consistency limit:** PostgreSQL and the POSIX evidence directory do not
share one transaction. The backup detects missing/changed files and refuses
`COMPLETE_LOCAL`; it does not claim a simultaneous filesystem snapshot.
Changing or deleting a referenced file after the dump but before copy will
fail validation when its bytes differ from the database hash. The next phase
must retain and verify the entire set when uploading it.

## Restore drill boundary

`python -m app.data_protection restore-test --backup-directory PATH
--evidence-root EMPTY_PATH --identity TEST_IDENTITY_FILE` accepts only:

- `FRCAIXINHA_ISOLATED_RESTORE=YES`, `APP_ENV=test`;
- PostgreSQL 16 on a loopback host, with an **empty, existing** database named
  `frcaixinha_restore_*`;
- an empty mode-0700 evidence directory and a mode-0600 age identity file.

The tool checks encrypted file sizes and SHA-256 before decrypting, validates
the custom archive, restores with `pg_restore --exit-on-error
--single-transaction --no-owner --no-acl`, checks Alembic head, ledger
sequence/guard trigger, ledger/evidence hash chains, reconciliation findings,
and every restored evidence file. Read-only checks use a PostgreSQL read-only
transaction. Reconciliation findings are reported, since pre-existing domain
findings must not be hidden or incorrectly attributed to restore damage.
The restore tool never starts the web API or worker and contains no provider
client. It rejects configured Mercado Pago, SMTP, WhatsApp or push credentials.
The CI database and age key are synthetic and temporary. A future real OCI
drill must also enforce a dedicated no-egress network and prove application
startup without invoking external effects.

The private age identity and the application's payout-destination encryption
key require separate, tested custody **outside** the VM. Neither belongs in
Git, the manifest, or the backup folder. A local `COMPLETE_LOCAL` set does not
meet the P0 off-VM or restore-drill acceptance criteria.
