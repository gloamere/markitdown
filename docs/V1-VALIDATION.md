# MarkItDown Web v1.0.0 validation

Verified functional and visual baseline:
`f57faf7e1ac5d5a46f5034dd15818f588aa72ce0` on `dev`.
The bounded production, Docling, browser and applicable visual checks have passed
on this baseline. Final version-bearing package/regression checks and exact-SHA
CI must pass before the immutable `v1.0.0` tag is created. The tag's target and its
CI results, recorded in [release PR #1](https://github.com/gloamere/markitdown/pull/1),
are the authoritative final verification once created; this document does not
claim that a later commit is already green.
Main remains at `4cc9fa1`; no server was deployed. Real-user research is deferred.

## Exact-baseline results

[Tests run 37582607189](https://github.com/gloamere/markitdown/actions/runs/37582607189)
passed all 24 jobs on `f57faf7`.
[Pre-commit run 37582607283](https://github.com/gloamere/markitdown/actions/runs/37582607283)
also passed on that SHA. All core, OCR and MCP matrix jobs passed; the upstream
OCR checks do not enable OCR in the Web app.

- Web: 647 passed / 4 explicit opt-in live-production skips in the ordinary
  suite. All four cases ran separately, without skips, in the production job
- Actual production boundary and lifecycle: 62 passed, including different host
  and sandbox namespace identities, intended-input access, other-job/home
  invisibility, source/root write denial, and native network/fork/session denial
- Real production standard conversion and independent preview: TXT, CSV, JSON,
  MD, DOCX, XLSX and PDF all passed (7/7)
- Production Docling: all seven stages passed, covering runtime/model identity,
  five pinned public model files, fresh probe, two-page preflight, conversion and
  independent preview, canonical three-page `page_limit` refusal, and cleanup
- Real sandboxed Chromium: all 12 named scenarios passed, covering invitation and
  six-character signup, repeat submission, preview safety, keyboard/view modes,
  clipboard denial fallback, MD/ZIP/manifest downloads, owner/admin authorization,
  stale navigation, settings conflicts/retention, account controls/audit privacy,
  logout/late replies, and 320/390-pixel responsive browser layouts
- Seven generated local-format evaluations, 9 browser-harness self-checks,
  8 installer tests, 202 offline regressions / 1 URL-download exclusion and
  59 DOM/state scenarios passed. Ruff, Black, mypy and syntax checks passed

Counts overlap; do not combine local and CI runs into an accuracy/capacity claim.
The ordinary suite's opt-in skips are not evidence by themselves; the separate
live production results establish those cases.

## Actual Mac visual and interaction review

A native Mac Chrome Incognito review on exact `f57faf7` inspected desktop and
390 × 844 responsive screenshots. Login, invitation, administrator settings,
audit, visible keyboard focus, upload queue and preview/source comparison were
readable, with no observed horizontal overflow. The rapid invitation double-click
created exactly one invitation and one audit entry. A TXT conversion produced the
expected result and consumed exactly one quota unit.

The synthetic test server, database and Incognito test window were cleaned up;
the existing normal service was unaffected. This is reviewed native-browser
visual and interaction evidence. The narrow viewport is responsive emulation,
not physical-mobile testing. Broader functional coverage comes from Linux CI;
the Mac review does not imply every browser, device or accessibility mode passed.
Raw Mac paths, screenshots, synthetic inputs and outputs are not published here.
See [BROWSER-TESTS](BROWSER-TESTS.md) for scope and evidence handling.

## Code-release finalization

1. **Verified on the baseline:** production cancellation, timeout and
   service-parent-death cases, tree death/reap, slot retention and no late
   publication. Scratch cleanup is checked for supervised cancellation/timeout
2. **Verified on the baseline:** the unchanged bounded production Docling profile,
   including pinned models, two-page conversion/preview, three-page refusal and
   cleanup
3. **Verified on the baseline:** real-browser scenarios plus applicable native
   Mac visual, invitation-fix, administrator-settings and narrow-screen review
4. **Required on the version-bearing commit:** source/wheel version parity,
   aggregate regression and exact-SHA CI, including the live production and
   Docling checks, before tagging that tested commit `v1.0.0`

This closes the implementation's existing scope, not a new feature roadmap.
Target-host suitability, intended-concurrency RAM/process/disk capacity,
operational crash recovery, TLS/DNS/proxy/systemd installation and rollout remain
separately authorized deployment work. No further corpus/capacity benchmark or
real-user study is required for this bounded code release. See
[V1_SCOPE](../V1_SCOPE.md). No main merge or deployment is authorized by this record.

## Prior evidence and retained failures

- Real local Docling: 30/30 checks passed with actual subprocesses, including
  pinned-model conversion, table/text facts, quota/retry, cancellation/reap and
  crash/restart cases. This run used local development mode, not the Linux
  production filesystem boundary
- Independent read-only review found and corrected the long-lived parent preview
  parser, backup intermediate directory permissions and proxy Host replacement
- Backup/recovery regressions cover deletion/deactivation/password-reset filtering,
  latest journal application and session/invitation invalidation
- Separate Mac aggregate at `9bdb787`: 501 Web tests passed / 5 platform/production
  skips, 8 installer tests, 202 offline formats / 1 URL exclusion, 59 DOM scenarios
  and lint/type/syntax checks. The Linux command-shape fixture was corrected; real
  Darwin/Windows production execution still fails closed
- Earlier actual Mac UI covered upload, preview/source/split, clipboard, downloads,
  cancellation, retry and owner/history isolation. A rapid invite double-click
  defect found on `e2cc22f` was fixed in `b52769e`, with DOM regression; the exact
  `f57faf7` native Mac retest above verified the fix
- CI `6c3d2db` on Ubuntu 24.04: 22/24 jobs passed. Chromium and Bubblewrap capability
  failures occurred before application testing. No security settings were changed
- CI `ae05471` on declared transitional Ubuntu 22.04: 22/24 passed. Both capability
  barriers cleared; an overly broad preview assertion and Docker's nonempty `/dev`
  export scaffolding failed. Harness/packaging fixes in `8ec3e78` made all jobs pass,
  without changing the application sanitizer, empty-target guard or security flags
- A separate read-only review found no concrete blocker in fresh-export preparation.
  It assumes a trusted disposable build workspace, not hostile same-UID mutation
  or same-device bind mounts

At historical checkpoint `8ec3e788bc5549dd5f41da5112c69a269a580e88`,
[Tests run 37579001936](https://github.com/gloamere/markitdown/actions/runs/37579001936)
passed all 24 jobs and
[Pre-commit run 37579001959](https://github.com/gloamere/markitdown/actions/runs/37579001959)
also passed. That run had 546 Web passes / 1 production opt-in skip, 44 production
checks and 7/7 standard formats; all 12 real-browser scenarios passed. Five Linux
core jobs each had 1,051 passes / 56 skips, two Windows x64/ARM jobs each had 1,059
passes / 48 skips, seven OCR jobs each had 108 passes, and seven MCP jobs each had
21 passes. Two workflow steps skipped Linux media-tool installation on Windows.
Those counts describe the older checkpoint, not the final release commit.

Raw synthetic inputs, outputs and detailed evidence are retained privately for the
owner. Checked-in harnesses regenerate fixtures and do not depend on unpublished
files. CI publishes summary logs, hashes and fixed provenance only, not screenshot,
download or document artifacts. All accounts/documents are synthetic; no user
feedback, broad quality rate, P95 or total-capacity estimate is inferred.

## Historical lifecycle and Docling correction

The `2b99a5e` harness checkpoint passed the complete local aggregate before
publication: 606 Web tests / 4 explicit live-production skips, 7/7 generated
formats, 9 harness self-checks, 8 installer checks, 202 offline regressions /
1 URL exclusion, 59 DOM scenarios and lint/format/type/syntax checks. Those
ordinary passes did not satisfy the skipped live-production cases.

[Run 37580441179](https://github.com/gloamere/markitdown/actions/runs/37580441179)
completed with 23/24 jobs passing; pre-commit also passed. The production suite
passed all 48 tests, including the three actual cancellation/timeout/parent-death
cases, and all seven standard formats. The Docling smoke verified all five model
files and passed its first five stages: runtime identity, manifest, live probe,
two-page preflight, actual conversion and independent preview. It then failed the
three-page-rejection assertion; cleanup verification was not reached. The harness
wrapped an unexpected refusal or acceptance as ValueError, losing the distinction.
The narrow diagnostic correction preserved a fixed rejection code or a separate
acceptance assertion. Independent portable reproduction also confirmed a sampler
exit race: a process could disappear between status and children reads. The
correction rechecks only a missing children file and accepts only confirmed
death/disappearance; missing roots still require supervisor exit confirmation.
Live, unknown, denied or malformed observations remain failures. This is a
demonstrated correctness fix, not proof of the prior CI failure's cause. Admission
limits and monitoring protections are unchanged. All 12 browser scenarios passed
in that run; the Docling rejection/cleanup stage had not yet passed.

The exit-race/diagnostic correction passed the complete local aggregate: 647 Web
checks / 4 explicit live-production skips, 7/7 formats, 9 harness self-checks,
8 installer checks, 202 offline regressions / 1 URL exclusion, 59 DOM scenarios
and lint/format/type/syntax. Independent read-only review found no concrete flaw
and passed 22 targeted portable exit/fail-closed regressions. Subsequent live CI
confirmed the corrected behavior; it does not retrospectively identify the prior
run's erased exception.

At `ba32c373`,
[Production job 112662201793](https://github.com/gloamere/markitdown/actions/runs/37581567228/job/112662201793)
passed 62 sandbox/lifecycle tests, seven standard formats and all seven Docling
stages, including `page_limit` refusal and cleanup. All 12 real-Chromium scenarios
and pre-commit passed. The complete exact-baseline CI result at the top supersedes
the earlier documentation's pending matrix status.

The historical candidate wheel was `markitdown_web-0.2.0-py3-none-any.whl`,
120,737 bytes, SHA256 `70671800483e66884e8c3c9627d1027d41a5f0250919d99fb7db3f32d21b31df`.
All 18 packaged source/static files byte-matched `ba32c373`, and version/license
metadata checks passed. This is retained candidate provenance only; it is not the
v1.0.0 artifact or evidence of the final version-bearing package checks.

## Version-bearing local package and regression check

Before publishing the 1.0.0 version-bearing commit, the full local aggregate
passed: 655 Web tests / 4 explicit opt-in live-production skips, 7/7 generated
formats, 9 harness self-checks, 8 installer checks, 202 offline regressions /
1 URL exclusion, 59 DOM scenarios, lint/format/type/syntax. The first aggregate
attempt found an existing test observer's procfs exit race: ESRCH was not treated
like ENOENT after a synthetic child had exited. The test-only fix accepts only
those confirmed-missing errors, preserves permission failures and observed live
states, and passed six added observer regressions. Application behavior is
unchanged by that correction; the earlier failed run was not counted as a pass.

`markitdown_web-1.0.0-py3-none-any.whl` built successfully (120,869 bytes), SHA256
`7cecfc5013fccdd06a28273ecf907daf924cfbb3eda57186ffb0f3483cc7d837`.
All 18 packaged source/static files and LICENSE byte-match the checkout. Package,
module and API versions are 1.0.0. Installation into an isolated target followed
by actual-wheel imports, index/static asset serving and synthetic API config
checks passed. This local evidence is separate from the required final commit's
CI; PR #1 records that exact result before tagging, avoiding a self-referential
post-test documentation commit.
