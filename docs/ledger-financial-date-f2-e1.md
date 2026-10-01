# Ledger financial date F2-E1

Migration `0106_event_financial_date_f2e1` adds nullable `DATE` columns to
`payment_settlements` and `payment_reversals`. It does not backfill existing
rows, change receipt versions or hashes, or add temporal consistency
constraints. Existing events remain `financial_date IS NULL` and retain their
legacy receipt contract.

This is an intermediate schema-only stage. No production writer assigns either
column and no reader consumes them. A populated date is reserved for the later
event-evidence gate, which must define and verify the corresponding receipt
version and the relationship to `confirmed_at` or `reversed_at` before any
writer is enabled. `settlement_component_version` remains unrelated to event
time.

The migration adds no index because no reader currently filters these columns.
Future reader work must measure its query plan before adding indexes. Downgrade
checks both tables first and refuses to remove the columns if either contains a
non-null date; it never deletes event data to make downgrade succeed.
