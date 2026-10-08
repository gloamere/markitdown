# Fixed synthetic export corpus

## Scope and inputs

`scripts/evaluate_web.py`, corpus version `synthetic-export-v1`, is a small,
author-generated engineering corpus. No private fixtures, customer documents,
credentials, screenshots or external resources are used. The generator and
expectations are published; generated inputs and measured artifacts remain local.
The original `fixtures()` helper still returns the same seven filenames/formats
and required facts. Office container metadata is now canonicalized.

The direct evaluator has 11 cases:

- Seven original TXT, CSV, JSON, MD, DOCX, XLSX and PDF format/fact checks
- One Markdown case preserving exact UTF-8 bytes: two heading levels, table
  rows and amounts, units/period, Chinese/café/emoji, escaped table pipe,
  task markers, inline and fenced code, literal asterisks/backticks/backslashes
- One valid textless PDF: expected `empty`, specifically the worker's `no_text`
  response; it does not yield a successful empty Markdown file
- One non-PDF byte stream with a PDF extension: expected `rejected`, specifically
  `invalid_pdf`
- One synthetic Office ZIP missing its document part: expected `rejected`,
  specifically `invalid_office`

`passed` means that case's stated outcome and assertions matched. It does not
mean that all documents converted successfully. Negative cases require the exact
safe error and actual worker-reported engine version. Generic `conversion_failed`,
resource/timeout errors, missing dependencies, unavailable infrastructure and
preview errors fail, including on intentionally invalid inputs. Job attempt error
codes are broader than parser codes; API checks use the specific safe error text
and execution metadata from the actual attempt, not the admission snapshot.

## Reproducibility contract

Every generated input has an explicit SHA-256 expectation in the script. For
DOCX/XLSX generation, ZIP entries are sorted, stored without compression, assigned
fixed portable metadata/time, and XLSX created/modified core-property times are
normalized **after** openpyxl saves. Setting workbook dates only before save is
insufficient because openpyxl replaces the modified date.

Regression tests move the ZIP and core-property clocks between different years,
prove that the pre-normalization packages actually differ, and then require all
14 input identities to equal the frozen expectations. A separate test changes
source-file mtimes through `ZipFile.write`. This normalization applies only to
these generated fixtures, never uploaded user documents.

Reproducibility is checked against the installed generator dependencies. A new
library serialization, content change or expectation change must be investigated;
do not simply replace hashes or weaken facts to make a run green. An intentional
corpus revision needs a reviewed version/expectation update. Outputs are measured,
not goldens silently updated to follow a converter. Execution durations, run dates,
job IDs, snapshots, manifests and ZIP container timestamps are live evidence and
are not expected to be identical between runs.

## Run locally

Use the repository's installed web-test environment:

```sh
.venv/bin/python -m pytest -q packages/markitdown-web/tests/test_export_corpus.py
OUT="$(mktemp -d .venv/synthetic-export-corpus-v1.XXXXXX)"
.venv/bin/python scripts/evaluate_web.py \
  --api-handoff --out "$OUT"
```

The evaluator's existing `--out` invocation remains supported. Without
`--api-handoff`, it runs the 11 direct cases and marks API handoff `not_run`.
The API option requires the existing web test dependencies, including TestClient
and httpx. It creates disposable synthetic accounts/application state, uses real
local-development workers, and removes that state on completion. It does not
contact a deployed service, install apps, enable Docling, or test production
sandbox isolation. Authentication, ownership, quotas and idempotency code are
unchanged. Exit status is nonzero if any requested case/archive/name check fails.

Output inside the checkout must be git-ignored; `.venv/` is already ignored.
An explicitly chosen local directory outside the checkout is also accepted.
Do not commit or upload the generated directory, even though its contents are
synthetic. Every run requires a new or precreated empty output directory. A
nonempty directory is rejected before conversion starts; nothing in it is
overwritten or deleted. After an interrupted run, choose another fresh directory
so partial artifacts cannot be mistaken for a previous successful report.

## Real export handoff evidence

The API run exercises all 11 corpus cases plus three Markdown variants: a second
same-named note with different bytes and two long Unicode names with different
bytes that truncate to the same download suggestion. It uses real authenticated
HTTP routes, queued jobs, parser/preview subprocesses and export responses.
There are 14 jobs, 11 successful Markdown outputs and three expected failed jobs.
Snapshot comparisons wait for the actual attempt's physical release as well as
terminal status; cleanup can legitimately add its release timestamp after result
publication. A stalled release fails the bounded wait instead of being ignored.

The harness checks:

1. Uploaded source SHA-256 equals the job, manifest and successful output metadata
2. Downloaded MD bytes equal job Markdown and preserve each case's required facts
3. Manifest job ID, filename, status, engine, submission snapshot and attempt
   history match the job; output SHA-256 matches both metadata and actual attempt
4. Actual worker-reported versions are present; unassessed quality remains
   unassessed, with no native document JSON or OCR claim
5. Two downloaded ZIPs respect the ten-job limit and contain only the requested,
   uniquely named, relative `.md` members, with no manifests, images or assets
6. Each ZIP member equals its individual download byte-for-byte; the exact member
   name maps to job ID, source SHA-256, Markdown SHA-256 and separate manifest
7. Both duplicate names and both truncated names appear in the same ZIP with
   distinct members and distinct job/source identities
8. Empty/rejected jobs have separate manifests but no output hash, MD download or
   successful ZIP; other users cannot read the tested downloads/manifests

The last ownership check runs in the focused pytest integration test. The CLI
harness does not independently create a second user for that check.

`evaluation.json` records corpus version, checkout commit/tree, tracked dirty
state, generator hash, application Python source hashes, installed dependencies,
actual per-case worker execution metadata, input/output SHA-256, durations,
assertions and failures. Installed versions are labeled as such; the local worker
uses the same Python environment and separately reports its actual engine/Python
version. This is not evidence of a Docling execution.

Direct inputs are under `inputs/`; direct Markdown keeps its established output
filenames. API downloads live under `api/jobs/<job-id>/` so identical suggested
names do not overwrite one another. Each directory holds the exact MD response
and separate `manifest.json` response. `api/archive-*.zip` holds the downloaded
Markdown-only archives. The report's API case/member records provide the exact
local filename/job/source/output-hash map for later consumer review.

## Separate consumer acceptance gate

The evaluator always records GitHub web and Obsidian as `not_run`. Byte equality,
local preview, Markdown syntax and browser-test renderers do not establish native
open/edit/reopen acceptance. A genuine check must identify the downloaded artifact
and hash, consumer/app version (or observable web build/date), platform and date,
then record what actually happened when opening, reviewing, editing and saving it.
Use the heading/table/unit/Unicode/escaped-code checklist in
[MARKDOWN-CONTRACT.md](MARKDOWN-CONTRACT.md). Record degradation without changing
expected facts. Do not install or drive a consumer application merely to replace
this pending gate, or publish private screenshots/evidence.
