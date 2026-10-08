# MarkItDown Web changelog

## 1.0.0 — 2026-10-07

First complete invitation-only Web release under the approved [v1 scope](../../V1_SCOPE.md).
This version belongs to `markitdown-web`; it does not rename the upstream
MarkItDown converter or Docling dependency versions.

### Product

- Invitation signup, 6–128-character passwords, login/session revocation and
  owner-only access to jobs, results, downloads and manifests
- PDF, DOCX, XLSX, TXT, MD, CSV and JSON input; batch queue/history, cancellation,
  idempotent submission/retry, durable attempts and source/profile snapshots
- Preview/source/compare modes, copy with manual fallback, UTF-8 Markdown,
  Chinese filenames, ZIP and factual output/attempt manifests
- Clear administrator account/invite controls, content-safe audit, read-only
  effective deployment settings and atomic versioned business defaults
- Expiry/deletion cleanup, stopped-service backup, independent recovery journal,
  restore filtering and interactive offline account recovery

### Isolation and optional Docling

- Production requires the fail-closed Linux `linux-bwrap-v1` filesystem,
  namespace, native syscall and bounded worker/preview supervision profile
- Local development remains explicitly nonproduction. Windows/macOS reject
  production mode rather than silently weaken it
- Optional Linux-only Docling uses five offline checksum-pinned model files;
  the whole PDF must be at most two pages and 10 MiB. Standard PDF has no explicit
  two-page cap. Both Web engine profiles keep OCR off
- Production resource limits and sampled RSS are not a per-job hard physical-RAM
  cgroup guarantee. Intended-host capacity remains a deployment check

### Verification

Functional/visual baseline `f57faf7e1ac5d5a46f5034dd15818f588aa72ce0` passed:

- All 24 CI test jobs plus pre-commit; 62 production sandbox/lifecycle tests
- Seven real standard formats and all seven production Docling smoke stages
- Twelve real sandboxed Chromium scenarios; 647 Web tests, with four live-runtime
  tests exercised separately in the production job; 59 DOM/state scenarios
- Actual Mac Chrome Incognito desktop and 390×844 responsive visual review,
  including invitation repeat-click behavior, administrator states and TXT flow

Responsive emulation is not physical-mobile testing. The final version-bearing
commit must independently pass package/source parity, complete regression and CI
before its immutable `v1.0.0` tag is created. Exact status is maintained in
[PR #1](https://github.com/gloamere/markitdown/pull/1) and the
[validation record](../../docs/V1-VALIDATION.md). Raw test documents/screenshots
are not published.

### Release boundary

No main merge, production deployment, DNS/TLS/account setup or real-user study is
included. Native Docling JSON, OCR and enterprise APIs remain the design's
consumer-evidence-dependent future work. No broad accuracy, throughput or
capacity claim is inferred from synthetic tests. GitHub's transitional Ubuntu
22.04 validation image retires on 2027-04-17 and requires migration.

Version-bearing local checks: 655 Web tests passed, four live-runtime cases were
explicitly skipped locally and remain mandatory in production CI; all existing
format, harness, installer, DOM and lint/type checks passed. The built 1.0.0 wheel
matched all 18 source/static files and license bytes and passed isolated-target
installation/import/asset/API smoke checks. An existing test-only procfs observer
was corrected for ESRCH during confirmed child exit; live/permission states
remain visible. Final tag creation still requires exact-commit CI confirmation.
