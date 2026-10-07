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

1. **Passed on 2b99a5e:** three live production lifecycle cases on the same
   unchanged profile: cancellation, timeout, service-parent death, tree death/reap,
   slot retention and no late publication; scratch cleanup is checked for the
   supervised cancellation/timeout cases. Final-version CI must rerun them
2. **Partially passed on 2b99a5e:** five pinned public model files, fresh probe,
   two-page preflight/conversion/independent preview. Three-page rejection failed
   and final cleanup verification was not reached. The earlier 30-case local run
   is not production proof
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

## Fixed follow-on CI result (2b99a5e)

[Run 37580441179](https://github.com/gloamere/markitdown/actions/runs/37580441179)
completed with 23/24 jobs passing; pre-commit also passed. The production suite
passed all 48 tests, including the three actual cancellation/timeout/parent-death
cases, and all seven standard formats. The Docling smoke verified all five model
files and passed its first five stages: runtime identity, manifest, live probe,
two-page preflight, actual conversion and independent preview. It then failed the
three-page-rejection assertion; cleanup verification was not reached. The harness
wrapped an unexpected refusal or acceptance as ValueError, losing the distinction.
The next narrow diagnostic correction preserves a fixed rejection code or a
separate acceptance assertion. Independent portable reproduction also confirmed
a sampler exit race: a process could disappear between status and children reads.
The narrow correction rechecks only a missing children file and accepts only
confirmed death/disappearance; missing roots still require supervisor exit
confirmation. Live, unknown, denied or malformed observations remain failures.
This is a demonstrated correctness fix, not proof of the prior CI failure cause.
Admission limits and monitoring protections are unchanged.
All 12 real-browser scenarios passed again. The lifecycle gate is demonstrated;
Docling rejection/cleanup and final exact-version checks remain open.

The exit-race/diagnostic correction passed the complete local aggregate: 647 Web
checks / 4 explicit live-production skips, 7/7 formats, 9 harness self-checks,
8 installer checks, 202 offline regressions / 1 URL exclusion, 59 DOM scenarios
and lint/format/type/syntax. Independent read-only review found no concrete flaw
and passed 22 targeted portable exit/fail-closed regressions. Live CI must still
confirm the corrected exact commit; these results do not retrospectively identify
the prior run's erased exception.
