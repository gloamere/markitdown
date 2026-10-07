# Linux production parser boundary for v1.0.0

This deployment contract and image recipe has passed the bounded code-release
runtime checks on the declared Linux CI target. Exact baseline
`f57faf7e1ac5d5a46f5034dd15818f588aa72ce0` passed 62 production boundary/lifecycle
checks, 7/7 standard formats and all seven production Docling stages in
[Tests run 37582607189](https://github.com/gloamere/markitdown/actions/runs/37582607189).
Final version-bearing CI remains required before the immutable release tag; see
[V1-VALIDATION](V1-VALIDATION.md) for finalization and historical evidence.

Target-host acceptance and deployment remain separately authorized work. These
results do not establish an unknown server's capacity or readiness for public
use. Initial cloud policy blocked production namespace launch; the separately
declared Ubuntu 22.04 CI target demonstrated the positive boundary without
changing or relaxing host security settings. No server was deployed.

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

The command always requests `--unshare-all --unshare-user --unshare-cgroup --die-with-parent
--disable-userns --cap-drop ALL --clearenv`. It never uses `--share-net`, `--unshare-user-try`,
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

## Supervision and target-host resource acceptance

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
is an error, never a fabricated zero. The initial cloud executor exposed
status/VmRSS but not the per-process children file: local Docling reported
direct-parser-process measurement, while production fails closed when required
wrapper-tree visibility is unavailable. Process-only sampling includes all threads
in the direct process and relies on Docling's seccomp denial of process creation; it is not a general
descendant-tree measurement.
Tmpfs caps do not replace host-wide capacity and admission planning. This revision
does not create delegated per-job cgroups or claim a per-job hard physical-RAM
limit. Intended-host hard resource/capacity and operational crash-recovery testing
remains a deployment-acceptance gate, separate from this code release. The code
release's bounded production-profile lifecycle checks passed on the declared
CI runtime at `f57faf7`; they must also pass for the final version-bearing SHA.
CI cannot establish an unknown server's capacity. Keep total service memory/process
limits as a separately approved operator deployment control; never change host
limits merely to make tests pass.

## Provision an image (separate approved operator work)

`deployment/runtime.Dockerfile` is a recipe targeting Linux x86_64 / Python 3.12.14.
It was built in the separately authorized Ubuntu 22.04 CI job on ae05471. That
run rejected Docker export mount scaffolding before parser launch. After the
confined packaging correction, 8ec3e78 passed 44 sandbox checks (including the
positive boundary) and all seven standard-format production conversions. These
results identify a CI runtime, not the intended deployment server.
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

Docker adds `/dev/pts`, `/dev/shm` and `/dev/console` when creating even a stopped
container. These are packaging scaffolds, not allowed persistent runtime state.
The CI-only `scripts/ci/prepare_runtime_export.py` reserves a fresh export root
before extraction, binds a one-shot marker to its inode/device and source image
ID, then removes only ephemeral `tmp/proc/dev` entries with no-follow traversal.
It refuses symlinked roots/targets, preexisting application state, a mismatched
identity or a second preparation. It is never called by the service or used to
repair an existing deployment. Diagnostics contain fixed target labels and counts
only. The application still rejects every nonempty mount target.

After an independently reviewed operator export, add `/markitdown-runtime.json`
inside the exported root (the confined CI preparer does this for its own export):

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
- `sandbox_bwrap=/usr/bin/bwrap` by default; a reviewed distro package or the
  approved, checksum-pinned non-setuid upstream build with all required flags.
  `MARKITDOWN_SANDBOX_BWRAP` selects that trusted startup path; Web clients cannot
  set it. The CI build installs only into runner temporary storage.
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


## Transitional CI compatibility target

The first exact-SHA CI run on Ubuntu 24.04 failed before application assertions:
Bubblewrap reported `Failed RTM_NEWADDR: Operation not permitted`; Chromium with
its sandbox enabled reported `No usable sandbox`. No protection was disabled.
That runner is not positively validated as a production profile.

A separately declared **transitional** `ubuntu-22.04` x64 job uses its default
host security policy and reviewed upstream Bubblewrap 0.13.0. Jammy's distro 0.6.1
lacks required `--size` and `--disable-userns` features. The selected upstream
release is non-setuid, built without privilege into runner temp, using the official
release asset SHA256 `4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765`
and release commit `719a4fd474d44b26906bcf2b1b0fb6eddd8d56d0`.

Every original protection is retained. Explicit `--unshare-user` and
`--unshare-cgroup` additionally make those namespace requirements mandatory:
upstream `--unshare-all` alone requests their try variants. A capability rejection
still fails the job; no sysctl, AppArmor, capabilities, privileged container,
unconfined option or sandbox-disabling flag is used. Selecting this declared test
OS is not a claim that the failed Ubuntu 24.04 host was repaired or made safe.

GitHub Ubuntu 22.04 deprecation started on 2026-09-17 and retirement is 2027-04-17.
This is temporary validation infrastructure requiring migration, not the intended
production operating-system decision. The actual deployment host remains unknown
and unverified. The positive 8ec3e78 isolation/standard-format result applies only to its tested
image, launcher digest and runner. Physical capacity/TLS remain deployment checks;
production Docling and real lifecycle integration passed on `ba32c373` and were
reverified on the exact `f57faf7` baseline. The historical runtime identities below
belong only to their named runs; each new build has its own recorded identity.

Sources: [runner retirement](https://github.com/actions/runner-images/issues/14254),
[Ubuntu namespace policy](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces),
[Jammy bwrap manual](https://manpages.ubuntu.com/manpages/jammy/man1/bwrap.1.html),
[official pinned release](https://github.com/containers/bubblewrap/releases/tag/v0.13.0).

## Executed CI runtime identity (8ec3e78)

[Production job 112654187833](https://github.com/gloamere/markitdown/actions/runs/37579001936/job/112654187833)
passed 44 boundary checks and seven standard-format conversions. It used GitHub's
Ubuntu 22.04.5 image `20260927.309.1`; the exact kernel was not in the fetched log.
The next run is a separately identified artifact, even if its source is unchanged.

- Runtime image: `sha256:e142399a18e8bb491eb0cf48e50847f1dca7c1753287bb5322e9cfd738851018`
- Python base: `docker.io/library/python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e`
- uv build stage: `ghcr.io/astral-sh/uv@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424`
- Bubblewrap binary: `sha256:4e57a5091d016ed0274d376a11f68b5c267f3752219031da3ee1fecab9e2654c`
- Preparation `trusted-docker-export-v1`: `/dev` had 3 entries before and 0 after;
  the other six required targets were already empty. All seven were directories

The source identity is recorded, not an assertion of byte-reproducible apt builds.
Neither this result nor the image marker authorizes an unknown deployment host.


## Executed lifecycle/Docling identity (ba32c373)

[Production job 112662201793](https://github.com/gloamere/markitdown/actions/runs/37581567228/job/112662201793)
passed 62 sandbox/lifecycle checks, all seven standard formats and all seven
production Docling smoke stages. The runner was Ubuntu 22.04.5, hosted image
`20260927.309.1`, runner `2.337.0`, kernel `Linux 6.8.0-1064-azure x86_64`.

Runtime image: `sha256:19fd4405ca54c9ccf9ed553d99dae68f0f02a861af1dbd6a61fcf8358c51e202`.
The verified Python/uv base digests, Bubblewrap source release and built-binary
hash match the preceding 8ec3e78 identities. No host security settings changed;
Bubblewrap was non-setuid. This additionally proves the finite synthetic
cancellation/timeout/parent-death cases and the pinned two-page Docling path,
three-page refusal and cleanup. It does not prove target-host capacity or TLS.
