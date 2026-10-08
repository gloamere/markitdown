# Keyboard, passive status, and renderer-zoom evidence

## Scope and current status (2026-10-08 UTC)

This is the fourth bounded document-workspace roadmap item. It changes frontend
focus recovery and passive live-region updates, makes the native connection page
responsive at narrow effective widths, and adds automated keyboard/zoom gates.
It does not establish accessibility certification or a WCAG conformance claim.

The implementation started from source tree
`39a1cd21096b86484e0d4b4013d255873c6da044`, matching remote baseline
`e064dbf0b945cf63ccfd09186304503d7dda458a`. The evidence below was collected on the
uncommitted accessibility working tree. Exact-commit browser/native CI and
separate human acceptance must be recorded before calling those gates passed.

## Demonstrated regressions and fixes

### Removed row focus

The original 94 DOM/state scenarios passed before edits. The DOM double previously
allowed `document.activeElement` to refer to a removed node, hiding a real-browser
failure mode. Its small removal model now detaches parent links, blurs removed
active descendants to body, and rejects focus on detached or disabled nodes.
Static HTML IDs remain connected roots; this is not a general DOM implementation.

With that model and the new regression assertions, the unchanged application
failed: `cancel pending focus should move to enabled View`, actual action
`undefined`, expected `select`. Starting a mutation replaced the focused Cancel,
Retry, or Delete button with a disabled button; the old fallback ran only when no
replacement existed.

The renderer now prefers an enabled replacement, then the same row's View button,
then another row's View. If deletion removes the last row while refresh is busy,
the current filter remains an enabled focus destination. Queue removal retains
the chooser fallback. Recovery happens synchronously from the current active
control on each render; there is no saved async focus target that could reclaim
focus after the user moves elsewhere.

New regression matrices cover:

- Pending Cancel, Retry, and Delete, followed by success or failure
- Same-row recovery when the target is not the first row
- Dismissed deletion confirmation, and confirmed deletion of the final row
- Newer navigation or another row selection during each pending action, followed
  by either success or failure, without focus or document selection being stolen
- Connected/enabled focus assertions, plus the original ZIP checkbox and queue
  removal scenarios under the stricter removal model

### Unchanged passive announcements

After fixing focus, the new unchanged-poll assertion failed against the old
status writes: `deployment-notice received identical live text on unchanged
polls`, mutation count `10`, expected `6` after two two-second polls. Direct
`textContent` assignments replace text nodes even when their string is unchanged.
The earlier implementation wrote five passive status strings twice per poll and
history text once per successful poll.

`statusText` now writes only changed strings for deployment, quota, workspace,
engine-selection, conversion-summary, and history-note regions. Explicit action
announcements retain their existing behavior. The regression asserts zero text
writes in all six regions across unchanged polls, then verifies meaningful job,
quota, queue, engine, deployment, failure, and recovery updates still appear.

Repeated screen-reader announcements are an **inferred risk**, not something
manually reproduced in a screen reader. The measured outcome is eliminated
redundant text-node mutation; assistive-technology behavior still needs review.

## Whole keyboard journey

`packages/markitdown-web/tests/browser_e2e.py` now has 13 automated scenarios.
The additional scenario uses actual Tab, Enter, arrow/Home, Space, and keyboard
text events for:

1. Skip link activation, with focus explicitly asserted on its `main` destination
2. Login/register roving tabs, username/password entry, and login submission
3. Keyboard chooser activation, adding two fixtures, and removing one queued file
4. Upload submission, completed-row selection, source-tab navigation and reading
5. Markdown download and checkbox-selected ZIP download, verifying exact bytes
   and a single quota charge

The skip-link destination has `tabindex="-1"`; tests no longer replace that check
with programmatic focus. A bounded Tab helper records focus stops without
`.focus()`. A stdlib harness check rejects pointer/fill/focus shortcuts in this
journey and helper. Legacy pointer-flow scenarios remain unchanged in purpose.

The chooser is triggered with Enter, but `FileChooser.set_files` injects synthetic
fixture bytes. This **does not test accepting or cancelling the native OS file
picker**. Browser download capture also does not accept an OS Save dialog.
Assertions that source text is focusable and matches the result do not prove
screen-reader reading quality. Failure capture includes the keyboard page.

## 200% renderer zoom and reflow

The connection page previously retained two columns below its 850px breakpoint;
its native 760px minimum width does not prevent a narrower CSS viewport at zoom.
The new 650px breakpoint stacks the story and connection form, lets header/footer
wrap, removes decorative rotation, and allows address/help text to wrap. No
workspace CSS was changed; its existing responsive layout is now explicitly
probed at the relevant effective widths.

The existing isolated Electron smoke tests call
`BrowserWindow.webContents.setZoomFactor(2)` and set native **content** widths:

- Setup: 1060 and 760 pixels, effective CSS widths 530 and 380
- Workspace: 1440 and 960 pixels, effective CSS widths 720 and 480

They assert the actual zoom factor and CSS width, no page horizontal overflow,
visible/uncovered primary controls, and real Tab reachability for enabled tested
controls. Setup additionally must stack its sections. Workspace probes cover
submission and completed-result/export states. The probes restore 100% zoom and
the original bounds in `finally`. Optional screenshot/JSON evidence identifies
this exact method and leaves visual review pending.

This is **automated renderer zoom**, not narrow-viewport emulation mislabeled as
manual zoom. It is also not a test of the OS/browser keyboard zoom shortcut,
physical mobile hardware, every zoom factor, every busy-state control, or a
screen reader. The setup/native zoom assertions have not executed successfully
in this preparation environment, so there is no claimed rendered
failed-before/fixed-after reflow result yet.

## Checks collected locally

Passed:

- Frontend DOM/state runner: **99 scenarios** (all original 94 plus five grouped
  regression scenarios)
- Browser harness stdlib self-checks: **10**
- Browser runner Ruff and Black checks, frontend app syntax, and both native smoke
  script syntax checks
- Web pytest suite: **719 passed, 4 skipped** (the skipped gates are not passes)
- Desktop `npm run check`: syntax checks plus **29 tests**
- `git diff --check`

Actual browser attempt: blocked before application scenarios because the pinned
Playwright Chromium headless executable is absent. Actual Electron setup-smoke
attempt: blocked during launch by `process_singleton_posix.cc` socket creation,
`Operation not permitted`. Neither blocker was bypassed; no tools were installed,
no sandbox flags weakened, and no normal user profile or Mac was used.

The browser keyboard scenario and Electron zoom assertions are therefore
**not run locally**, not passed. Earlier native/browser successes on other source
versions do not cover this change.

## Remaining acceptance gates

1. Run all 13 browser scenarios and both native smoke flows on the exact published
   commit using their normal sandbox-enabled CI hosts; preserve failures as
   blocking. Confirm dimensions, keyboard stops, download parity, and restoration
   after the zoom probes
2. Review actual setup/workspace pixels at the recorded widths and 200% renderer
   zoom for clipping, wrapping, readable labels, visible focus, and vertical
   scrolling. Automated bounds and screenshots alone are not visual acceptance
3. On the authorized Mac, manually exercise login, file chooser accept/cancel,
   add/remove/submit/read/export, Cancel/Retry/Delete confirmation and pending
   focus, tab order, and real keyboard zoom/return to 100%
4. Separately check VoiceOver (and other supported assistive technology as
   appropriate): names/roles, roving tabs, passive polling silence, meaningful
   updates, output reading, busy/failure states, and focus after dialogs

Security, request-deadline, identity/epoch, idempotency, ownership, CSRF, parser,
corpus, dependency, workflow, and build-evidence boundaries are unchanged.

## Exact-commit CI update

Application commit `98938961e9adf7234a11a610251f63db4faf5ee8` subsequently passed
all three workflows. The sandbox-enabled Chromium run passed all 13 scenarios,
including the new keyboard journey. Mac source and assembled-app smoke runs
passed both setup and workspace 200% renderer-zoom probes; Windows passed the
setup probe. See [the consolidated acceptance record](ROADMAP-ACCEPTANCE.zh-CN.md)
for exact workflow links and remaining manual/native/downstream gates. These CI
results supersede only the automated execution gate, not visual or assistive-
technology acceptance. The earlier cloud-local launch blockers remain accurately
recorded above.
