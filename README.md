# Release Receipt Check

An offline Python 3.11–3.14 CLI (receipt schema version 2). Compare a reviewed release record with a separate
deployment observation: did the recorded observation identify the expected
deployment ID, revision, artifact SHA-256 and environment? Does the packet show named checks
executed against that deployment ID, environment, revision and digest, a separate
reviewer recorded the same four fields, and a prior artifact is pinned by digest?

```sh
python3 release_receipt_check.py examples/reviewed.json examples/observed-pass.json
python3 release_receipt_check.py examples/reviewed.json examples/observed-fail.json
python3 release_receipt_check.py examples/reviewed.json examples/observed-unknown.json
python3 -m unittest -v
```

The CLI prints JSON: **PASS** (exit 0) means consistent on this contract;
**FAIL** (exit 1) means explicit failure, invalid data or contradiction;
**UNKNOWN** (exit 2) means missing, unreadable, skipped or uncertain evidence.
FAIL takes priority over UNKNOWN. Only exit 0 satisfies the check. Use the first
two example files as the schema v2 field reference. Collect direct test exit codes, not
a wrapper status. Ensure the reviewed list of required check names is complete.
Use a stable revision identifier and lowercase, unprefixed SHA-256. All examples
are invented and no example digest refers to a real deployment.

Use a stable deployment ID assigned to one rollout attempt. Required checks in
this prototype are target-bound observations, not stand-ins from the build job;
each PASS or FAIL entry must identify the same deployment, environment, revision,
and digest and provide its direct exit code and completion time. An observation
is the final receipt capture: its timestamp must be at or after review and each
recorded PASS or FAIL check. Times must include seconds and an explicit timezone
(Z or a numeric offset); comparisons use UTC and equal timestamps are allowed.
This is ordering of recorded claims, not cryptographic freshness, trusted time,
proof of uniqueness, or protection against replay of a whole matching packet.

A PASS is **not proof of release or infrastructure safety**. The tool trusts
what its inputs report. It doesn't authenticate evidence, identities,
signatures, who supplied a check list, or that the observation was independently
taken. Its local checks cannot show that an evidence reference exists, that a
test exit came from the claimed process, that the rollback remains available
or works, or that a customer workflow works end to end. Obtain the review
record and post-deploy observation separately from sources you trust. Use
existing signing/attestation and deployment systems for their own jobs.
The tool does not contact a host, execute anything from a receipt, or deploy.

## Compatibility and license

The CI workflow tests Python 3.11, 3.12, 3.13, and 3.14 on Ubuntu. Check
the workflow results for the revision you plan to use; configuration alone
is not evidence of a passing CI run. The source is licensed under [MIT](LICENSE).
