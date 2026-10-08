# Synthetic real-browser regression gate

`packages/markitdown-web/tests/browser_e2e.py` is an opt-in Playwright runner for
the invited-user and administrator flows in [V1_SCOPE.md](../V1_SCOPE.md).
It uses a real Chromium process, real local HTTP, and the actual application,
conversion workers, rendered UI, and browser download events. It is deliberately
outside pytest's automatic `test_*.py` collection.

## Current evidence status

The real sandboxed Chromium runner passed all 12 scenarios on exact
`f57faf7e1ac5d5a46f5034dd15818f588aa72ce0` in
[Tests run 37582607189](https://github.com/gloamere/markitdown/actions/runs/37582607189).
Its nine local self-checks also passed. A separate native Mac Chrome Incognito
review on the same SHA completed the applicable desktop and 390 × 844 responsive
visual and interaction checks described below. Broader functional coverage is
provided by Linux CI. The earlier preparation VM socket restriction was not
bypassed; the CI target uses its normal host policy and keeps Chromium's sandbox
enabled.

Passing syntax or self-checks is not a passed browser gate. Automated assertions
and unreviewed screenshots do not establish visual signoff. A harness run needs
separately recorded pixel review for its visual claims; the Mac review is distinct
from the CI harness's captured-but-unreviewed PNGs. Neither replaces production
isolation, real-model tests, target-host/TLS acceptance, external Markdown-consumer
verification, or user research. Final version-bearing CI must still pass before
creating the immutable release tag; see [V1-VALIDATION](V1-VALIDATION.md).

### Native Mac review on f57faf7

Actual desktop and 390 × 844 responsive screenshots were inspected in native Mac
Chrome Incognito. Login, invitation, administrator settings, audit, keyboard
focus, upload queue and preview/source comparison were readable, with no observed
horizontal overflow. A rapid invitation double-click created exactly one
invitation and one audit entry. A TXT conversion produced the expected result
and consumed exactly one quota unit.

The disposable test server, synthetic database and Incognito test window were
cleaned up; the existing normal service was unaffected. Raw Mac paths,
screenshots, synthetic inputs and outputs remain private. The 390 × 844 view was
responsive emulation, not a physical-mobile test; the 320-pixel layout has the
separate automated CI coverage below, not this native visual signoff.

## Authorized runner and installation

Use a disposable, non-root, supported Linux CI runner with Python 3.12, working
loopback sockets and Chromium's native sandbox already supported. An authorized
developer workstation can also run it with its own disposable browser; no normal
browser profile, existing cookies, credentials or running application is used.
Do not direct this harness at production. There is intentionally no `--base-url`
or existing-data-directory option.

From the repository root, in a dedicated virtual environment:

```sh
python3.12 -m venv .venv-browser
. .venv-browser/bin/activate
export ORT_DISABLE_TELEMETRY=1
python -m pip install -r requirements-browser-test.in
python -m playwright install chromium
python packages/markitdown-web/tests/browser_e2e.py --self-check
python packages/markitdown-web/tests/browser_e2e.py --out .venv-browser/evidence
```

These are instructions for an approved capable runner, not commands executed
during preparation. Use a fresh/empty output directory per run; the runner
refuses to overwrite prior evidence. It never installs dependencies itself.
Missing system libraries, denied sockets or unavailable sandbox capabilities
must fail the job and remain visible as a blocker. Do not add `--no-sandbox`, run
privileged/root, change host security settings or route around access denials.

[Playwright 1.63.0 on official PyPI](https://pypi.org/project/playwright/1.63.0/)
was verified on 2026-10-07. The input pins that Python package and includes the
existing application dependency lock. It is **not a new, fully resolved,
hash-locked browser environment**. The installed Playwright version is checked
at runtime; use its bundled Chromium download, not a system Chrome override.
The runner records the actual Chromium version. Playwright's documented
[`chromium_sandbox` launch option](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-option-chromium-sandbox)
is explicitly set to `True`; the harness supplies no extra Chromium flags.

For CI, allow roughly 15 minutes including cold dependency/browser installation.
The included PR workflow uses a dedicated job on a transitional Ubuntu 22.04 sandbox-capable runner with
public pass/fail summaries only. Raw screenshots/downloads are not uploaded. If Actions
or the job has not executed, the gate is **not run**, not passed. Do not use `continue-on-error` to represent a failing browser
job as passing. The workflow is maintained separately from this runner.

## Isolation and synthetic inputs

- Creates one fresh temporary private SQLite/data directory and binds Uvicorn to
  a reserved `127.0.0.1` ephemeral port; proxy-header trust is disabled
- Programmatically seeds only a synthetic administrator with a random ephemeral
  password. Normal members are invited, registered and logged in through the UI
- Uses an exact six-character synthetic member password, fresh nonpersistent
  browser contexts and no saved storage state, traces, user credentials or
  connected accounts
- Explicitly selects the application's local evaluation mode and disables the
  optional Docling profile for this test. This does not alter an existing server
  or claim production parser isolation
- Writes small real UTF-8 Markdown/text fixtures, including Chinese filenames,
  Unicode, table data, long text, and inert untrusted HTML/link/image input
- Keeps browser page requests on the test origin; attempted external page
  requests are blocked and recorded as failures
- Saves downloads only to fixed harness-chosen names below `--out/downloads`;
  never trusts a suggested filename as a destination and never extracts ZIPs
- Closes contexts, Chromium, the application lifespan and its workers, then
  removes the disposable data/fixtures. Only requested evidence/downloads remain

Browser API assertions use `fetch` in the actual authenticated page. Mutation
requests obtain the real CSRF token from that page's session and send the normal
request marker; cookies, Origin enforcement and owner checks are not bypassed.
Timing tests hold a real server detail response and release it after newer UI
activity. Clipboard tests deliberately deny both automatic copy mechanisms to
exercise accessible manual selection. These injections are recorded and do not
substitute fake API or conversion results.

## Automated coverage

1. Administrator UI login, keyboard auth tabs and skip link
2. UI invitation creation at 1-hour/7-day bounds, visible expiry, token dismissal,
   one-time reuse denial, invalid TTL API rejection, UI registration/login with
   six-character passwords in independent member contexts
3. Real file selection/upload/conversion, double-click guarding, exactly one
   submission/job/quota charge, completion status, safe rendered preview
4. Keyboard source/compare tabs, selected/focused states, source parity and
   denied-copy fallback that focuses and selects all source text
5. Real browser MD, ZIP and manifest downloads; exact Markdown parity, safe ZIP
   member names, source digest, attempts/config facts and capability claims
6. Member and administrator denial of another owner's detail, download,
   manifest, archive, retry, cancel and delete; member denial of admin reads
7. Reload/history restoration, navigation away/back and delayed old detail
   responses failing to overwrite a newer selection
8. Actual admin field values, read-only deployment controls, dismissed save,
   accepted versioned changes, real two-tab 409 conflict and refresh, unchanged
   old account/job defaults, changed future account/job defaults and retention
9. Account quota/file limits, disabled UI self-deactivation plus server rejection,
   another member's deactivation/session revocation and content-free audit checks
10. Logout while a real detail response is held, immediate private-content clear,
    server-session revocation, refused download, Back/reload privacy and no
    local/session storage content
11. Actual full-page PNGs at 1440-pixel desktop and 390-/320-pixel mobile widths,
    mobile keyboard tab checks, document/admin layouts and page horizontal
    overflow assertions

This first runner does not claim coverage of every timing permutation, native
clipboard success, every browser/OS, screen-reader behavior, cancellation/reap
timing, successful retry, or naturally waiting hours for TTL expiry. Existing
service/unit tests cover other contracts; successful browser flows and visual
review must be tied to the exact reviewed commit. The verified functional and
visual baseline above does not assert a pass for an untested later release SHA.

## Evidence and visual sign-off

The process exits 0 only if every automated scenario completes, otherwise 1.
`evidence.json` is written before live work and updated after each scenario. It
records timestamps, source commit/dirty state, platform, Python and actual
browser/Playwright versions, sandbox launch settings, per-scenario
`not_run`/`running`/`passed`/`failed` status, JavaScript/network failures and timing
injections. A server or browser startup error is an unsuccessful run, not a skip.
`failure.txt` and best-effort real failure screenshots are retained on failure.

Screenshot and download records include relative paths, byte sizes and SHA-256
digests. Screenshots additionally record viewport/scroll width. Expected images
include desktop login, preview, comparison, copy fallback, administration,
logged-out state, and mobile login/comparison/administration. PNG creation and
DOM/layout checks do **not** establish visual quality. The JSON deliberately
keeps `visual_review_status: not_reviewed` and `release_gate_complete: false`.

A reviewer should inspect the saved pixels and record a separate signed-off
note linked to the same commit/run and screenshot hashes. Check at least:

- Legible Chinese text, hierarchy, contrast and visible focus
- Full labels/controls, usable touch targets, no clipping/overlap or offscreen
  actions, including the narrow 320-pixel administrator forms
- Preview/source comparison scrolling and visible long filenames/table content
- Copy-fallback notice and selected source, useful expiry/config information
- Cleared logged-out content and no invitation value left after dismissal

Screenshots and downloads contain only synthetic data, but treat the evidence
directory as private until reviewed. No browser storage-state/cookie export is
produced. Do not publish a failure artifact containing unexpected sensitive data.

Historically, the initial Ubuntu 24.04 job failed at sandbox launch, before any
application checks; later Ubuntu 22.04 runs passed all 12 scenarios.
The declared Ubuntu 22.04 target keeps Chromium sandboxing enabled and uses its
normal host policy; it retires on 2027-04-17 and is not a long-term baseline. Failure
is still blocking. Private Mac review supplies visual evidence; unreviewed CI
screenshots do not count as visual acceptance. The exact `f57faf7` native review
above supplies the applicable reviewed visual evidence for the v1 baseline.
