# v1 candidate engineering status

The initial completed application/UI checkpoint was
`b52769e3022179960d25d97da8443d9c00f3b355`. Subsequent changes explicitly require
user/cgroup namespaces and allow a reviewed startup launcher path; they need
exact-commit CI verification. This is a **candidate**, not a completed
production release. The installed application version remains 0.2.0 until the
v1.0.0 engineering gates pass. Main remains at 4cc9fa1; no server was deployed.

## Executed

- Complete cloud check-web before the final test-only portability patch: 503
  Python tests pass, one explicit production-isolation
  test skipped because a reviewed runtime/capable namespace environment is absent
- 8 installer checks and 202 upstream offline regressions pass; one arXiv network
  download test deliberately deselected
- 59 frontend DOM/state scenarios pass; these are not visual/browser acceptance
- 7 actual standard-format synthetic key-value checks pass
- 30/30 current real local Docling checks pass, zero unreached; full input/output/
  runtime/source hashes and retained earlier failures in the evidence directory
- Wheel built; 18 source/static files byte-match checkout. Version remains 0.2.0
- Ruff, Black, Mypy, JavaScript/shell syntax pass
- Independent read-only review found and corrected the long-lived parent preview
  parser, backup intermediate directory permissions and proxy Host replacement;
  nine existing ordinary correction checks passed, with limitations retained

Raw synthetic input/output files and detailed run logs are retained separately
for the owner and have not been published to GitHub. The checked-in harnesses
regenerate synthetic fixtures at runtime and do not depend on unpublished files.
All samples/accounts are synthetic. No generalized accuracy, P95, total capacity,
real user feedback, hard RSS guarantee or production-isolation result is inferred.

## Blocking / unexecuted

1. Existing cloud bwrap probe fails NETLINK_ROUTE socket Operation not permitted.
   Required production profile stays unavailable, no fallback or permission changes.
2. Production image recipe has not been built here; positive per-job isolation,
   intended-host hard physical-memory/capacity and production Docling checks remain.
3. Actual browser rendering/clipboard/download/mobile checks are a separate gate.
   This cloud Chromium also failed socket creation; no security bypass was used.
4. The repository owner has now enabled GitHub Actions (verified 2026-10-07).
   Run 37576232027 on 6c3d2db completed: 22 of 24 jobs passed. Browser sandbox launch
   and host namespace preflight failed. Pre-commit passed. A declared transitional
   Ubuntu 22.04 compatibility target now awaits its own exact-commit results.
5. nginx/systemd are templates; actual TLS/proxy/target host need separate approved
   staging/rollout work. Production deployment itself is outside this code task.

The browser gate is being assessed on an authorized separate computer. A bounded
transitional Ubuntu 22.04 CI job is prepared for existing namespace capability and runtime
building, using only contents:read and no secrets, privileged containers, sysctl
changes or unconfined options. Any denial is a job failure, never a silent skip.

Final naming/tagging and a request for user acceptance must not describe these
unexecuted engineering checks as the skipped real-user study. See [V1_SCOPE](../V1_SCOPE.md).

Actual Mac browser engineering QA found a rapid double-click invitation bug on
e2cc22f. Fix b52769e preserves the visible one-time token until explicit dismissal;
59 DOM cases include this regression. Actual Mac retest is tracked separately.

## Cross-platform engineering check

A separate Mac run at 9bdb787 completed the aggregate check with exit 0: 501 Web
tests passed, 5 platform/production tests skipped, 8 installer tests, 202 offline
format tests passed and 1 URL download test excluded; 59 DOM scenarios and
lint/type/syntax checks passed. These are engineering tests, not completed
browser/visual acceptance. The prior command-shape fixture failure was corrected
by modelling Linux only inside that unit fixture and testing explicit Darwin/
Windows production refusal. Production behavior was not weakened.

Actual Mac UI checks completed upload, preview/source/split, clipboard, MD/ZIP/
manifest downloads, cancellation, retry and account/history isolation. Invitation
fix retest, full administrator settings and narrow-screen review remained pending
when user browser interaction required the test session to pause.

The portability repair was additionally checked in the cloud with 104 focused
passes and one explicit intended-host isolation skip. Counts from overlapping
suites are not added together. The prepared GitHub jobs still require exact-commit execution and result review.
The existing pull_request trigger has no branch/path filters; neither a main
merge nor expanded workflow permissions is needed to request the next run.

On 6c3d2db, hosted Web CI passed 505 tests with 1 explicit production test skipped,
7/7 generated format checks,8 harness self-checks,8 installer tests and202 offline
regressions (1 URL test excluded). All core Python/OCR/MCP matrix jobs passed.
The two capability failures occurred before image construction or real browser
flows. No failed gate is hidden by continue-on-error or a skip.
