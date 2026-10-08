# Markdown output and consumer checks

v1 emits UTF-8 Markdown, a separately sanitized preview, and a factual JSON
manifest. The manifest is provenance/attempt metadata, not DoclingDocument JSON.
It includes only actually available source, configuration and output identities;
missing historical versions remain unknown. It does not assert extraction quality.

## Supported handoff

- Copy and `.md` download preserve the generated Markdown bytes
- UTF-8 Chinese filenames are offered through Content-Disposition filename*
- Batch ZIP members are unique, relative `.md` names with no traversal components
- Headings, lists, code, ordinary tables and Unicode are exercised by synthetic
  fixtures; table spans/complex layout may be flattened by the converter
- No images or relative assets are saved by this profile. Image fidelity and a
  self-contained image archive are not promised
- Browser preview removes active links/images/raw HTML. Downloaded Markdown is
  still untrusted and may render differently in a consumer
- No remote signed URLs are required for reopening the saved synthetic MD samples

## Reproducible small evaluation

Run `scripts/evaluate_web.py` with the web test Python. The versioned
`synthetic-export-v1` corpus keeps the original seven generated format samples
(TXT/MD/CSV/JSON/DOCX/XLSX/PDF) and their required facts, then adds structured
Unicode/escaped-code Markdown, an empty PDF, and two malformed documents.
Office ZIP metadata and core-property dates are normalized after generation;
reviewed input hashes catch generator/dependency drift. Tests vary the actual
pre-normalization ZIP/core clocks and source-file modification times.

The evaluator runs actual conversion subprocesses. Expected successful, empty,
and rejected outcomes are explicit: a timeout, crash, missing dependency or
generic conversion failure never counts as an expected empty/rejected result.
Every case and failure stays in the report. Source commit/tree/code hashes,
corpus version, installed library versions, executed worker versions, input/output
hashes and elapsed time are recorded. These author-generated examples are
engineering checks, not independent annotations, a user study, a capacity
estimate or a general accuracy benchmark.

Add `--api-handoff` to verify the real authenticated upload, queue, worker and
download routes. It compares individual MD responses and Markdown-only ZIP
members against job content and separately downloaded JSON manifests, including
source/output SHA-256 identities, attempts, duplicate names and long Unicode
names that truncate to the same suggestion. This does not change the ZIP format
or prove native consumer rendering. See [the corpus runbook](EXPORT-CORPUS.md).

Generated synthetic inputs, Markdown, ZIPs, JSON manifests and measured reports
are retained locally in ignored directories; raw input/output artifacts are not
published to this repository. Actual GitHub web/Obsidian consumer acceptance must be
recorded separately with target/version/date and observed checks. A local preview
or known Markdown syntax alone is not proof those applications opened the output.
Obsidian is not installed or driven automatically by this repository.

## Consumer review checklist

Regenerate the synthetic Chinese-note `.md` with the evaluation harness, then
open the owner-held output in the chosen destination and compare:
heading; two table rows; values 1234.50/80.25; unit/period; escaped pipe remaining
inside one cell; Chinese characters; code escaping; list/task markers. Reopen the
saved file with networking off where the consumer supports it, then make a small
edit and save. Record any degradation rather than modifying expected facts to pass.
Do not load arbitrary local/remote HTML or image resources from unknown documents.

Specification references (syntax context, not product acceptance evidence):
[GitHub Flavored Markdown](https://github.github.com/gfm/),
[Obsidian Markdown](https://help.obsidian.md/Editing+and+formatting/Basic+formatting+syntax)

## Portable suggested export names

Markdown downloads keep a `.md` extension within a 180-byte UTF-8 filename budget.
Windows reserved device names receive a `document-` prefix. ZIP members retain the
existing job-ID/index suffix so different jobs do not collide after truncation.
Source display names, hashes and Markdown bytes are unchanged. Desktop save-dialog
suggestions also preserve `.json`/`.zip` extensions and remove trailing dot/space
aliases. This does not guarantee an arbitrary user-selected destination path is writable
or bypass the operating system's overwrite confirmation.
