# Ledger financial date F2-E2: temporal receipt evidence

F2-E2 adds opt-in evidence formats for future temporal events. Settlement
receipt `v6` is common to Contribution, LoanInstallment, and
AgreementInstallment; `obligation.type` selects the applicable evidence
block. Payment reversal receipt `v2` carries its own temporal date and is
independent of the settlement receipt version.

For settlement `v6`, `PaymentSettlement.confirmed_at` is the persisted source
timestamp. For reversal `v2`, the source is `PaymentReversal.reversed_at`.
The corresponding `financial_date` is the civil date of that timestamp in
`America/Belem`. A stored DATE is used as-is; it is not converted through a
timezone again. SQLite naive timestamp values are normalized as UTC only in
the new temporal evidence path.

Temporal settlement evidence includes the settlement and Payment identities,
obligation identity, persisted confirmation time, amounts/decomposition,
applicable state revisions, exact LedgerEntry evidence, and MemberFinancialEntry
evidence. Loan principal is represented by its real MemberFinancialEntry; this
format does not create a LedgerEntry for principal. Empty ledger evidence is
valid for an obligation with no expected ledger component. Reversal evidence
binds its own persisted date/time, original settlement receipt identity/hash,
obligation-specific v1 evidence, linked original/compensating ledger rows, and
the applicable original/compensating member-financial rows.

Legacy settlement receipts `v1` through `v5` and reversal receipt `v1` retain
their existing serializers and hashes. Migration 0107 does not backfill or
rewrite existing receipts. Legacy receipt versions require `financial_date`
to remain NULL; settlement `v6` and reversal `v2` require a non-NULL date.
No financial-date index is added because economic readers do not use these
columns yet.

The receipt SHA-256 is a canonical integrity/reproducibility hash. It is not a
keyed signature and does not protect against a privileged actor rewriting both
the stored payload and hash.

This change does not activate temporal writers. Operational settlement and
reversal flows continue producing the existing legacy versions, and economic
readers continue using their current classification. Any writer rollout must
wait for a separate reader-compatibility gate.
