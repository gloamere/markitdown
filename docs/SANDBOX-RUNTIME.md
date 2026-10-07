# Linux production parser boundary (v1 implementation; deployment gate open)

This is a **deployment contract and unbuilt image recipe**, not evidence that the
service is ready for public use. The 2026-10-07 authorized cloud verification could
run application/local conversion tests, native seccomp tests and cancellation
checks. The production namespace launch was blocked by that environment's
existing security policy. No host settings were changed or relaxed.

## Local and production are deliberately different

- `deployment_mode=local` is the default for development and existing synthetic
  conversion tests. Standard conversion works on Linux/Mac; it has a disposable
  worker, timeout, reduced environment, best-effort Unix limits and Python network
  guards. **It is not a filesystem isolation boundary for untrusted users.**
- `deployment_mode=production` requires an approved dedicated runtime root and
  mandatory Linux namespaces. Failed setup/probe/launch does not run a local
  parser, silently switch engines or skip a protection. Both engines report
  unavailable until their actual production probe succeeds.
- Docling is Linux-only in both modes. It retains its native seccomp filter,
  offline pinned models, two-page / 10 MiB admission, CPU-only operation and OCR
  disabled. Standard PDF conversion has **no explicit page-count cap** and still
  has its separate size, time and output limits.

## Fixed profile: `linux-bwrap-v1`

Only these host objects are exposed:

1. One operator-reviewed runtime image root, mounted read-only at `/`
2. Exactly seven reviewed worker/support code files, each read-only under
   `/code/markitdown_web`, with no service data directory mount
3. One read-only source file at `/input/source.<approved suffix>` (no source in
   availability probes)
4. For Docling only, the five individually validated, checksum-pinned model files
   from `docling_adapter.ASSETS`, at their exact `/models` paths
5. One precreated job-private writable result inode at `/output/result.json`

A new `/tmp` tmpfs is capped at 512 MiB. Input/code/model/output directory scaffolds
are separate 1 MiB tmpfs mounts, remounted read-only after exact file binding; the
result inode remains writable. `/proc` is from the new PID namespace and remounted
read-only. `/dev` is bubblewrap's private minimal device set, not host `/dev`,
and its filesystem is remounted read-only to prevent extra scratch files.

The command always requests `--unshare-all --die-with-parent --disable-userns
--cap-drop ALL --clearenv`. It never uses `--share-net`, `--unshare-user-try`,
`--not-a-security-boundary`, a host root mount, container socket, arbitrary client
command, client mount, client environment or client model path. Bubblewrap's PID-1
reaper remains enabled. All file descriptors other than standard null streams
are closed before launch.

Before parser imports, an irreversible no-new-privileges/seccomp filter rejects
native networking, process forks, non-thread clones, session/group changes,
exec, new namespaces, mount operations, ptrace and several other escape surfaces.
Threads remain allowed; clone3 falls back with ENOSYS. The worker verifies its
native socket call is denied. The network namespace is mandatory even though
this syscall filter also denies egress.

Source and output paths must be regular, nonsymlink files. The parent checks output
file type, hardlink count, bounded byte length, JSON object type, fixed safe error
codes, Markdown size, and Docling metadata. It **ignores all child HTML** and
passes bounded Markdown to a separate trusted renderer process with raw HTML
disabled and no images, links' attributes or URI protocols. The supervisor then
checks a linear fixed-tag/no-attribute grammar without building a Markdown/HTML
tree. Renderer output remains bounded. Output JSON is never an arbitrary file
manifest.

## Supervision and remaining resource gate

Both engines and the independent preview process poll cancellation and wall time.
One shared absolute deadline (45 seconds standard / 60 seconds Docling) includes
conversion and preview; rendering has an additional 8-second stage cap. This is
not a fresh extra allowance after a converter uses its full budget. They retain the scheduler's physical
slot through TERM, unconditional process-group KILL, leader reap, and confirmation
that no live group member remains. This covers a leader exiting before a
SIGTERM-ignoring descendant. Production prevents parser process creation/group
escape and bubblewrap's PID namespace reaps/kills descendants. Parent death kills
the sandbox. Private scratch stays registered as active through complete reap and
cleanup; maintenance must exclude `sandbox.active_workspaces()`.

- Standard: 45 s wall, 35 CPU s, 1.5 GiB address-space ceiling
- Docling conversion: 60 s wall, 90 CPU s, 8 GiB address-space ceiling, two CPUs,
  sampled RSS <= 3 GiB; production requires complete-wrapper-tree visibility,
  local mode measures only the directly supervised parser process
- Docling preflight/probe: 8 s wall, 5 CPU s, 1 GiB address space, sampled RSS <= 512 MiB
- Independent preview: 5 CPU s, 384 MiB address space, 8 s maximum stage wall
  (further reduced by the attempt's remaining shared deadline)
- Production workers: 128-process/thread RLIMIT_NPROC, 16 MiB result file limit,
  zero core dumps; descriptor limits 64 standard / 128 Docling
- Markdown <= 2 MiB, independently generated HTML <= 4 MiB

RLIMIT_AS is **not** a per-job physical-memory cgroup. RLIMIT_NPROC counts tasks for
the operating-system UID, so use a dedicated service account and validate thread
headroom at intended concurrency. RSS checks are sampled and can miss peaks. A live process with unavailable VmRSS
is an error, never a fabricated zero. The cloud executor exposes status/VmRSS
but not the per-process children file: local Docling reports direct-parser-process
measurement, while production fails closed when required wrapper-tree visibility
is unavailable. Process-only sampling includes all threads in the direct process
and relies on Docling's seccomp denial of process creation; it is not a general
descendant-tree measurement.
Tmpfs caps do not replace host-wide capacity and admission planning. This revision
does not create delegated per-job cgroups or claim a per-job hard physical-RAM
limit. Intended-host hard resource/capacity and crash-recovery testing remains a
G2 release gate. Keep total service memory/process limits as a separately approved
operator deployment control; never change host limits merely to make tests pass.

## Provision an image (separate approved operator work)

`deployment/runtime.Dockerfile` is a recipe targeting Linux x86_64 / Python 3.12.14.
It has not been built or positively tested in this restricted cloud environment.
Provide immutable digests for the official Python 3.12.14 slim-bookworm image and
uv 0.12.19 image. No credentials should be included in build arguments or context.
Pin/review the apt package snapshot as part of release packaging; upstream apt
state is not pinned by the Python lock files. Save SBOM, base/tool digests, lock
hashes, package versions and licenses with the release.

The recipe installs standard converter dependencies constrained by the repository
lock and the exact Docling frozen lock into `/opt/docling`. It excludes application
account data and does not download weights. Model provisioning stays separate;
use the existing audited `scripts/docling` procedure only when authorized.

Build and export the stopped image using the operator's existing approved OCI
build tooling. Do not give the web account Docker/Podman sockets or permissions.
Place the exported root outside the repository, service data and user homes;
review it for keys, tokens, caches, documents, account state and unexpected mounts.
A root filesystem copied from the host, `/usr`, a virtualenv or a home is forbidden.
Runtime image contents are trusted deployment artifacts, not arbitrary user input.

Add `/markitdown-runtime.json` inside the exported root:

```json
{"profile":"linux-bwrap-v1","image_id":"sha256:<64 lowercase hexadecimal characters>"}
```

`image_id` is the recorded immutable source image identity, not a self-signed claim
that the exported directory has been independently hashed. Protect and audit the
export against subsequent modifications; the marker alone does not establish
supply-chain trust. Each `/input`, `/output`, `/code`, `/models`, `/tmp`, `/proc` and
`/dev` directory must exist, be nonsymlink and empty before any job. The image's
interpreter must resolve within that image. Absolute host-oriented virtualenv
symlinks are rejected; use copied executables or image-internal relative symlinks.

Configure the service with a secure HTTPS origin and:

- `deployment_mode=production`
- `sandbox_runtime_root=/absolute/reviewed/runtime-root`
- `sandbox_bwrap=/usr/bin/bwrap` (a reviewed distro package with required flags)
- `sandbox_python=/usr/bin/python3`
- `sandbox_docling_python=/opt/docling/bin/python`
- Existing `docling_enabled` and checksum-pinned `docling_models` if enabling Docling

`docling_python` remains the local-development host executable; production uses
`sandbox_docling_python` inside the image. No running conversion downloads models.
The app must run a single scheduler under the intended unprivileged service UID.

## Required positive and negative verification on the intended Linux host

Required existing capabilities: working unprivileged user/mount/PID/network/IPC/
UTS/cgroup namespaces, supported bubblewrap (including `--disable-userns`, `--size`
and `--remount-ro`), libseccomp in the image, `/proc` visibility sufficient to
supervise only the service's process trees, and disk/RAM/thread headroom under the
intended host controls. A capability error is a blocking result, not authorization
to change userns/sysctl/LSM/seccomp/network policy.

Run the focused application suite and then explicitly enable the production test:

```sh
MARKITDOWN_TEST_RUNTIME_ROOT=/absolute/reviewed/runtime-root \
  .venv/bin/pytest -q packages/markitdown-web/tests/test_sandbox_contract.py
```

Without that variable, the positive production test is explicitly skipped; this
is **not** a passed isolation gate. With it, namespace denial is a hard failure.
The test verifies host/sibling/home invisibility, source/root write denial, native
network and fork/session denial, while reading the intended input. Also run real
standard format conversions and Docling two-page conversion/preflight against
production configuration. Exercise cancel, timeout, leader exit, service death,
restart, memory/process/disk exhaustion, two simultaneous owners, output symlink/
FIFO/oversize cases and deletion-before-result. Verify no live tasks or accessible
scratch/late artifacts remain and no slot releases before reap. Perform all fault
injection in disposable synthetic-data staging, never against customer inputs.

The exact initial cloud capability probe was:

```sh
/usr/bin/bwrap --unshare-all --die-with-parent \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
  --proc /proc --dev /dev /usr/bin/true
```

It returned exit 1: `bwrap: loopback: Failed to create NETLINK_ROUTE socket:
Operation not permitted`. This was a read-only/existing-capability probe, not the
production mount profile. No alternate namespace route, permission expansion,
host security change or public deployment was attempted.
