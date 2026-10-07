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

Run `scripts/evaluate_web.py` with the web test Python. It generates all inputs
locally (TXT/MD/CSV/JSON/DOCX/XLSX/PDF), runs actual conversion subprocesses and
checks explicitly listed text/number/unit facts. Every failure stays in the report.
Source hashes, engine/library versions, output hashes and elapsed time are recorded.
The seven samples are engineering checks, not independent annotations, a user
study, a capacity estimate or a general accuracy benchmark.

Generated synthetic Markdown and its measured report are retained separately
for the owner; raw input/output artifacts are not published to this repository. Actual GitHub web/Obsidian consumer acceptance must be
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
