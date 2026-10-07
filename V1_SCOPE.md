# MarkItDown Web v1 scope and acceptance contract

Approved implementation baseline: `0f3f46504e9bae7e6d19ab27006f1bf2384ce4f0` on `dev`.
The 2026-10-07 instruction authorizes completing the delivered product design,
checkpoint pushes, self-testing, and the v1.0.0 name after engineering gates pass.
It skips real-user research; it does **not** waive engineering or security gates.
`main`, production servers, DNS, credentials and host security settings are unchanged.

## Product boundary

An invitation-only, single-instance document-to-Markdown workspace with usable
registration, login, upload, task history, cancellation, retry, preview, copy,
download, and administrator controls. Standard MarkItDown supports PDF, DOCX,
XLSX, TXT, MD, CSV and JSON input. Docling is an optional Linux-only short-PDF
profile: whole document at most 2 pages / 10 MiB. Those two limits do not apply
to standard PDF conversion. Both profiles have OCR off. Output is Markdown,
safe preview and a factual manifest, not native DoclingDocument JSON.

## Delivered-design traceability

| Item | v1 implementation and validation | Explicit boundary |
| --- | --- | --- |
| BT00 P0 | Fix baseline, preserve and rerun the reviewed portability tests, publish this scope and reproducible checks | The old publication cancellation is superseded only for the newly approved work |
| BT01 P0 | 6–128 character password policy across service/API/CLI/UI; old hashes remain valid; rate limits and session revocation; interactive owner recovery | No default password, real credential entry, public reset endpoint or automatic reactivation |
| BT02 P0 | Account/invite controls with scope, actual values, consequences, limits, effective time and minimal audit; read-only deployment configuration | Admin status does not grant document access; no password/token/content in audit |
| BT03 P0 | Owner-scoped idempotent submissions, durable attempt records, source/profile/config snapshots and explicit retry/recovery counting | Legacy missing versions are unrecorded; retries use current approved profile without rewriting original submission |
| BT04 P0 | Fail-closed Linux per-job filesystem, process, network and resource boundary; isolated preflight and conversion | A capability failure disables the production profile; local development mode is explicitly nonproduction |
| BT05 P0 | Cancellation/expiry/delete revoke publication; physical slot waits for process reap; independently checked Markdown and sanitized preview | Client status does not prove process exit; parser-produced HTML is untrusted |
| BT06 P0 | Orphan scratch cleanup, diagnostics, consistent stopped-service backup with hashes, restore filtering and invalidated sessions; T0 backup/T1 deletion/T2 restore test | Backup retention is separate; no secure-erasure or deletion-of-downloaded-copies claim |
| BT07 P0 | Invited user and administrator browser flows, desktop/mobile layouts, interruption/repetition and clipboard failure paths; synthetic accounts | DOM tests are not browser or visual acceptance; no research participant feedback is fabricated |
| BT08 P0 | Deployment package, safe HTTPS/Host/Origin contract, preflight and rollback/runbook; local packaging tests | Actual target-host inspection, TLS installation and production rollout require separate authorization; not part of this code release |
| BT09 P1 | Synthetic real-format conversion/evaluation harness, failures included and key value checks | Rights-cleared 64-input human quality pilot and 320-input holdout expansion deferred with real-user research; no broad accuracy/throughput claims |
| BT10 P1 | Markdown contract, Chinese filenames, table/escaping/Unicode and offline artifact tests; identify tested consumer/version and untested consumers | No nonexistent images/assets/provenance claimed; preview is not consumer acceptance |
| BT11 conditional P2 | Deferred by the approved design's consumer-evidence gate | Rich native JSON, coordinate/source editing, OCR and enterprise API are future product decisions, not skipped tests of promised v1 features |
| BT12 P1 | Bounded atomic versioned default quota, default file-size limit and retention settings; defaults affect new accounts, retention affects new jobs | Concurrency/model/network/isolation/host paths are read-only; no arbitrary live runtime changes |

## Work packets and checkpoint policy

1. Baseline, scope and CI wiring
2. Accounts, audit and versioned business controls
3. Task admission, attempts, cancellation and output metadata
4. Linux parser isolation and trust boundary
5. Invited-user and admin interface
6. Cleanup, backup/restore, deployment and compatibility evidence
7. Independent security review, complete regression, browser validation and release packaging

Each finished section is committed to `dev` and remote SHA verified. The draft PR
is updated with exact checks and remaining gates. No `[skip ci]` in new commits;
local checks are never represented as GitHub Actions results. Any workflow-write
authorization limitation is disclosed rather than bypassed.

## Release gates

- Full local lint, formatting, typing, web/installer/offline-format tests and
  frontend checks pass on the exact candidate; wheel contents match source
- Synthetic two-account end-to-end browser flows and applicable visual checks
- Positive production isolation attacks and cancellation/reap checks on a
  capable, authorized Linux environment; negative fail-closed tests everywhere
- Backup/restore deletion and deactivation filtering; no old sessions survive
- Independent security review findings resolved or explicitly blocking
- Exact-commit CI checked; failures, blocked and never-run checks distinguished
- Version/tag v1.0.0 only on the final tested commit; no main merge or deployment

Target-host authorization and real-user studies are separate from a code release.
An unavailable required test environment remains an engineering gate, not a pass.
