# v1 candidate engineering status

Latest completed CI checkpoint: `8ec3e788bc5549dd5f41da5112c69a269a580e88`.
This is still a **candidate**. The installed application version remains 0.2.0
until the fixed code-release closure checks below pass. Main remains at 4cc9fa1;
no server was deployed. Real-user research is deferred; engineering checks are not.

## Exact-checkpoint results

[Tests run 37579001936](https://github.com/gloamere/markitdown/actions/runs/37579001936)
passed all 24 jobs. [Pre-commit run 37579001959](https://github.com/gloamere/markitdown/actions/runs/37579001959)
also passed. No failed, cancelled, skipped or unexecuted jobs remain on this SHA.
The two skipped workflow steps are Linux media-tool installation on Windows.

- Web: 546 passed / 1 explicitly skipped positive production test. That test runs
  separately, without a skip, in the production job
- Actual production boundary: 44 passed; all seven namespace identities differ
  from the host; intended input is readable, other-job/home files invisible,
  source/root writes denied, native network/fork/session creation denied
- Real production standard conversion and independent preview: TXT, CSV, JSON,
  MD, DOCX, XLSX and PDF all passed (7/7)
- Real sandboxed Chromium: all 12 named scenarios passed, covering invitation and
  six-character signup, repeat submission, preview safety, keyboard/view modes,
  clipboard denial fallback, MD/ZIP/manifest downloads, owner/admin authorization,
  stale navigation, settings conflicts/retention, account controls/audit privacy,
  logout/late replies, and 320/390-pixel browser layouts
- Five Linux core Python jobs: each 1,051 passed / 56 skipped. Two Windows x64/ARM
  jobs: each 1,059 passed / 48 skipped. Seven OCR jobs: each 108 passed. Seven MCP
  jobs: each 21 passed. These upstream OCR checks do not enable OCR in the Web app
- Seven generated local-format evaluations, 9 browser-harness self-checks,
  8 installer tests, 202 offline regressions / 1 URL-download exclusion and
  59 DOM/state scenarios passed. Ruff, Black, mypy and syntax checks passed

Counts overlap; do not combine the local and CI runs into an accuracy/capacity
claim. Browser assertions and captured-but-unreviewed pixels are not visual signoff.

## Fixed remaining code-release closure

1. Three live production lifecycle cases on the same unchanged profile: running
   cancellation, timeout, and service-parent death; verify tree death/reap, no
   premature slot reuse, no late publication and private scratch cleanup where
   applicable. Existing local/mocked checks are useful but not this evidence
2. One bounded production Docling integration: the existing five pinned public
   model files, fresh probe, two-page preflight/conversion/independent preview,
   and three-page rejection. The earlier 30-case local run is not production proof
3. Complete applicable actual visual review and the paused Mac invitation-fix,
   administrator-settings and narrow-screen checks
4. Final version-bearing source/wheel parity, aggregate regression and exact-SHA
   CI, then tag that tested commit v1.0.0. No main merge or deployment

This list closes the implementation's existing gates, not a new feature roadmap.
Target-host suitability, intended-concurrency RAM/process/disk capacity, operational
crash recovery, TLS/DNS/proxy/systemd installation and rollout remain separately
authorized deployment work. No further corpus/capacity benchmark or real-user
study is required for this bounded code-release closure. See [V1_SCOPE](../V1_SCOPE.md).

## Prior evidence and retained failures

- Real local Docling: 30/30 checks passed with actual subprocesses, including
  pinned-model conversion, table/text facts, quota/retry, cancellation/reap and
  crash/restart cases. This run used local development mode, not the Linux
  production filesystem boundary
- Independent read-only review found and corrected the long-lived parent preview
  parser, backup intermediate directory permissions and proxy Host replacement
- Backup/recovery regressions cover deletion/deactivation/password-reset filtering,
  latest journal application and session/invitation invalidation
- Separate Mac aggregate at 9bdb787: 501 Web tests passed / 5 platform/production
  skips, 8 installer tests, 202 offline formats / 1 URL exclusion, 59 DOM scenarios
  and lint/type/syntax checks. The Linux command-shape fixture was corrected; real
  Darwin/Windows production execution still fails closed
- Actual Mac UI covered upload, preview/source/split, clipboard, downloads,
  cancellation, retry and owner/history isolation. A rapid invite double-click
  defect found on e2cc22f was fixed in b52769e, with DOM regression; final actual
  Mac retest remains in the fixed closure list
- CI 6c3d2db on Ubuntu 24.04: 22/24 jobs passed. Chromium and Bubblewrap capability
  failures occurred before application testing. No security settings were changed
- CI ae05471 on declared transitional Ubuntu 22.04: 22/24 passed. Both capability
  barriers cleared; an overly broad preview assertion and Docker's nonempty /dev
  export scaffolding failed. Harness/packaging fixes in 8ec3e78 made all jobs pass,
  without changing the application sanitizer, empty-target guard or security flags
- A separate read-only review found no concrete blocker in fresh-export preparation.
  It assumes a trusted disposable build workspace, not hostile same-UID mutation
  or same-device bind mounts

Raw synthetic inputs, outputs and detailed evidence are retained privately for the
owner. Checked-in harnesses regenerate fixtures and do not depend on unpublished
files. CI publishes summary logs, hashes and fixed provenance only, not screenshot,
download or document artifacts. All accounts/documents are synthetic; no user
feedback, broad quality rate, P95 or total-capacity estimate is inferred.

The fixed follow-on lifecycle/Docling harness checkpoint passed the complete local
aggregate before publication: 606 Web tests / 4 explicit live-production skips,
7/7 generated formats, 9 harness self-checks, 8 installer checks, 202 offline
regressions / 1 URL exclusion, 59 DOM scenarios and lint/format/type/syntax checks.
These ordinary passes do not satisfy the four skipped live production cases;
those and the Docling smoke are mandatory in the configured production CI job.
