# Synthetic Phase A backup and restore drill

This harness creates its own disposable PostgreSQL 16 container. It migrates
only that container, inserts one obviously synthetic workflow evidence file,
creates a genuine Phase A backup, and restores it into a second empty database
in the same container. It never calls Google Drive, the API, or the worker.

## Prerequisites and command

Use a local Docker daemon available through `/var/run/docker.sock`, Python with
`backend/requirements.txt`, PostgreSQL 16 `pg_dump` and `pg_restore`, and
`age`/`age-keygen` v1.2.1. The Docker daemon must already have the
`postgres:16-alpine` image; the harness uses `--pull=never`. Create distinct
mode-0700 directories outside the repository and run from `backend`:

```sh
python -m app.synthetic_phase_a_drill \
  --package-root /private/synthetic-phase-a-packages \
  --identity-root /private/synthetic-phase-a-identities
```

The harness takes no database address, password, OAuth credential, or age key
from the operator. It refuses an inherited `DATABASE_URL_FILE`, builds a fresh
child environment with `APP_ENV=test`, and sets both `DATABASE_URL` and `PG*`
for the same randomly named test database on a Docker-assigned loopback port.
Before migration, backup, and restore, it checks those settings and compares
the server system identifier obtained over the published port with the one
obtained inside the exact container it created. It requires server and client
version 16. The restore target is a separate empty `frcaixinha_restore_*`
database. Child commands run outside `backend` so its `.env` is not loaded.

The private age identity is generated for this run and stays in the identity
directory. It is never included in the Phase A package. The output JSON gives
the final package path, identity path, backup ID, and size/SHA-256 of a
deterministic transport envelope. Only after the restore and existing read-only
integrity checks pass are the package and identity moved to their final paths.
Keep both paths private and preserve the identity separately; losing it makes
the package unusable. Neither path belongs in Git or Google Drive together.

## Manual persistent CI artifact

The manual workflow `.github/workflows/synthetic-phase-a-persistent.yml` is
restricted to `main`. Before using it, configure the non-secret repository
variable `BACKUP_AGE_OPERATOR_RECIPIENT` with the operator's age public
recipient. The workflow fails closed if it is absent or invalid. It never
receives the operator's private identity.

For this workflow, the harness creates a second, ephemeral runner identity.
Phase A encrypts the PostgreSQL archive and every evidence file to both the
operator recipient and the runner recipient. The restore-test decrypts the
same ciphertext with only the runner identity; the operator can later restore
the downloaded package with the operator identity. The runner identity stays
in its separate temporary directory and is not published.

Only after backup, restore, integrity checks, envelope inspection, and
checksum validation pass does the workflow upload the `.frcaixinha.tar`
envelope and a non-secret receipt. The artifact is named by run ID and backup
ID and retained for 7 days. Download it promptly, verify its SHA-256, inspect
and extract the envelope into a private directory, then run the existing
isolated restore-test with the operator identity before any later transport
operation. The GitHub artifact is a staging mechanism, not durable backup
storage. The ordinary Backend CI drill remains ephemeral and does not publish
its output.

The Backend CI job runs this drill with temporary runner directories. Its
output is evidence that the harness works on an isolated PostgreSQL 16 runner;
the CI job does not publish its identity or package as artifacts. A CI package
does not constitute a persistent local backup for a later transport test.
