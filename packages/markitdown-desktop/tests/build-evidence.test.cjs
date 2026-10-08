"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const asar = require("@electron/asar");
const evidence = require("../scripts/build-evidence.cjs");
function git(root, ...args) { return execFileSync("git", ["-C", root, ...args], { encoding: "utf8", stdio: "pipe" }).trim(); }
function write(file, value) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, value); }
function writeJson(file, value) { write(file, JSON.stringify(value, null, 2) + "\n"); }
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "markitdown-evidence-test-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const project = path.join(root, "packages/markitdown-desktop");
  const pkg = { name: "markitdown-desktop", version: "1.1.0-dev.1", private: true, main: "src/main.cjs",
    scripts: { start: "electron ." }, devDependencies: { "@electron/asar": "3.4.1", electron: "44.6.0", "electron-builder": "26.15.3" },
    build: { asar: true, artifactName: "MarkItDown-${version}-${os}-${arch}-unsigned.${ext}" } };
  writeJson(path.join(project, "package.json"), pkg);
  writeJson(path.join(project, "package-lock.json"), { version: pkg.version, packages: { "": { version: pkg.version } } });
  write(path.join(project, "src/main.cjs"), "console.log('synthetic fixture');\n");
  write(path.join(project, "src/setup.html"), "<p>Synthetic fixture</p>\n");
  write(path.join(project, "src/icon.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  write(path.join(project, "LICENSE"), "Synthetic license\n");
  write(path.join(root, ".gitignore"), "dist/\n");
  write(path.join(root, "packages/markitdown-web/src/markitdown_web/__init__.py"), '__version__ = "1.0.0"\n');
  write(path.join(root, "packages/markitdown-web/pyproject.toml"), '[project]\nversion = "1.0.0"\n');
  write(path.join(root, "packages/markitdown/src/markitdown/__about__.py"), '__version__ = "0.1.8"\n');
  git(root, "init"); git(root, "config", "user.email", "synthetic@example.invalid"); git(root, "config", "user.name", "Synthetic evidence test");
  git(root, "config", "core.autocrlf", "false"); git(root, "add", ".");
  git(root, "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic evidence fixture");
  const commit = git(root, "rev-parse", "HEAD");
  const environment = { GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: "synthetic/desktop", GITHUB_RUN_ID: "1234", GITHUB_RUN_ATTEMPT: "2", GITHUB_JOB: "desktop", GITHUB_SHA: commit };
  return { root, project, pkg, environment, snapshot: evidence.sourceSnapshot(root, environment) };
}
async function archive(fixture, mutate = () => {}, options = {}) {
  const staging = path.join(fixture.project, "dist/fixture"); fs.mkdirSync(staging, { recursive: true });
  for (const file of fixture.snapshot.resources) write(path.join(staging, file.path), fs.readFileSync(path.join(fixture.project, file.path)));
  const pkg = { ...fixture.pkg }; for (const key of ["scripts", "build", "devDependencies"]) delete pkg[key];
  writeJson(path.join(staging, "package.json"), pkg); mutate(staging, pkg);
  const destination = path.join(fixture.project, "dist/app.asar"); await asar.createPackageWithOptions(staging, destination, options); return destination;
}
test("clean source captures exact commit, tree, versions and runtime bytes", t => {
  const f = fixture(t);
  assert.equal(f.snapshot.source.commit, f.environment.GITHUB_SHA);
  assert.equal(f.snapshot.source.tree, git(f.root, "rev-parse", "HEAD^{tree}"));
  assert.equal(f.snapshot.source.clean, true); assert.equal(f.snapshot.versions.desktop, f.pkg.version);
  assert.equal(f.snapshot.versions.service_declared, "1.0.0"); assert.equal(f.snapshot.versions.core_declared, "0.1.8");
  assert.equal(f.snapshot.resources.length, 4);
  for (const item of f.snapshot.resources) assert.match(item.sha256, /^[a-f0-9]{64}$/);
});
test("dirty tracked, staged, and untracked source is rejected", t => {
  const f = fixture(t); fs.appendFileSync(path.join(f.project, "src/main.cjs"), "// dirty\n");
  assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /clean/); git(f.root, "add", ".");
  assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /clean/); git(f.root, "reset", "--hard", "HEAD");
  write(path.join(f.root, "untracked.txt"), "synthetic"); assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /clean/);
});
test("same-size edits with restored timestamps cannot inherit a clean commit", t => {
  const f = fixture(t);
  git(f.root, "config", "core.trustctime", "false");
  const file = path.join(f.root, "packages/markitdown-web/src/markitdown_web/__init__.py");
  const timestamp = Math.floor(Date.now() / 1000) - 3600;
  fs.utimesSync(file, timestamp, timestamp);
  git(f.root, "update-index", "--refresh");
  fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace("1.0.0", "9.0.0"));
  fs.utimesSync(file, timestamp, timestamp);
  assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /clean|source bytes/);
});
test("hidden index flags cannot mask changed source", t => {
  const f = fixture(t); const relative = "packages/markitdown-desktop/src/main.cjs";
  for (const flag of ["assume-unchanged", "skip-worktree"]) {
    git(f.root, "update-index", `--${flag}`, relative); assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /hidden/);
    git(f.root, "update-index", `--no-${flag}`, relative);
  }
});
test("a new clean commit or a changed CI run cannot reuse an earlier snapshot", t => {
  const f = fixture(t); write(path.join(f.root, "new-source.txt"), "new committed source"); git(f.root, "add", ".");
  git(f.root, "-c", "commit.gpgsign=false", "commit", "-m", "Changed source fixture");
  assert.throws(() => evidence.sourceSnapshot(f.root, f.environment), /CI commit/);
  const after = evidence.sourceSnapshot(f.root, { ...f.environment, GITHUB_SHA: git(f.root, "rev-parse", "HEAD") });
  assert.throws(() => evidence.assertSameSource(f.snapshot, after), /changed/);
  assert.throws(() => evidence.assertSameSource(f.snapshot, { ...f.snapshot, ci: { ...f.snapshot.ci, run_id: "999" } }), /changed/);
});
test("ordinary Windows CRLF checkout still binds normalized Git source", t => {
  const f = fixture(t); git(f.root, "config", "core.autocrlf", "true");
  for (const resource of f.snapshot.resources.filter(value => !value.path.endsWith(".png"))) {
    const file = path.join(f.project, resource.path); fs.unlinkSync(file);
    git(f.root, "checkout", "--", `packages/markitdown-desktop/${resource.path}`);
  }
  const windows = evidence.sourceSnapshot(f.root, f.environment);
  assert.equal(windows.source.commit, f.snapshot.source.commit); assert.notDeepEqual(windows.resources, f.snapshot.resources);
});
test("source/lock version drift fails closed", t => {
  const f = fixture(t); f.pkg.version = "1.1.0-dev.2"; writeJson(path.join(f.project, "package.json"), f.pkg); git(f.root, "add", ".");
  git(f.root, "-c", "commit.gpgsign=false", "commit", "-m", "Mismatched source metadata");
  assert.throws(() => evidence.sourceSnapshot(f.root, {}), /lockfile/);
});
test("every packed runtime resource and package field matches clean source", async t => {
  const f = fixture(t); const checked = evidence.verifyAsar(await archive(f), f.project, f.snapshot);
  assert.equal(checked.status, "verified"); assert.equal(checked.desktop_version, f.pkg.version); assert.equal(checked.resources.length, 4);
});
for (const [label, mutate] of [
  ["same-size source corruption", staging => { const main = path.join(staging, "src/main.cjs"); fs.writeFileSync(main, fs.readFileSync(main, "utf8").replace("synthetic", "corrupted")); }],
  ["missing runtime file", staging => fs.unlinkSync(path.join(staging, "src/setup.html"))],
  ["unexpected runtime file", staging => write(path.join(staging, "src/injected.cjs"), "injected")],
  ["wrong desktop version", (staging, pkg) => writeJson(path.join(staging, "package.json"), { ...pkg, version: "0.0.0" })],
  ["wrong entry point", (staging, pkg) => writeJson(path.join(staging, "package.json"), { ...pkg, main: "src/setup.html" })],
  ["extra import mapping", (staging, pkg) => writeJson(path.join(staging, "package.json"), { ...pkg, imports: { "#private": "./src/setup.html" } })],
]) test(`packaged ${label} fails`, async t => {
  const f = fixture(t); const packed = await archive(f, mutate); assert.throws(() => evidence.verifyAsar(packed, f.project, f.snapshot), /runtime|package.json/);
});
test("unpacked asar resources are explicitly refused", async t => {
  const f = fixture(t); const packed = await archive(f, () => {}, { unpack: "*.cjs" });
  assert.throws(() => evidence.verifyAsar(packed, f.project, f.snapshot), /unpacked/);
});
test("executed runtime versions are separate and mismatches fail", t => {
  const f = fixture(t); const raw = { platform: "win32", arch: "x64", electron: "44.6.0", node: "24.19.0", chrome: "150.0.0.0", v8: "15.0.1", secret: "DO_NOT_COPY" };
  const runtime = evidence.validateRuntime(raw, f.snapshot, evidence.TARGETS.windows);
  assert.equal(runtime.status, "executed"); assert.equal(runtime.chromium, raw.chrome); assert.equal(runtime.secret, undefined);
  assert.throws(() => evidence.validateRuntime({ ...raw, electron: "1.0.0" }, f.snapshot, evidence.TARGETS.windows), /Electron/);
  assert.throws(() => evidence.validateRuntime({ ...raw, arch: "arm64" }, f.snapshot, evidence.TARGETS.windows), /architecture/);
  assert.throws(() => evidence.validateRuntime({ ...raw, platform: "darwin" }, f.snapshot, evidence.TARGETS.windows), /platform/);
  assert.throws(() => evidence.validateRuntime({ ...raw, node: "/private/path" }, f.snapshot, evidence.TARGETS.windows), /runtime version/);
});
test("both target payloads reject changed asar or executable bytes", t => {
  const f = fixture(t);
  for (const [platform, target] of Object.entries(evidence.TARGETS)) {
    const app = path.join(f.project, "dist", platform);
    for (const relative of [target.asar, ...target.binaries]) write(path.join(app, relative), "synthetic original");
    const hashes = evidence.verifyPayload(app, target); assert.deepEqual(evidence.verifyPayload(app, target, hashes), hashes);
    write(path.join(app, target.asar), "synthetic modified"); assert.throws(() => evidence.verifyPayload(app, target, hashes), /payload differs/);
    write(path.join(app, target.asar), "synthetic original"); write(path.join(app, target.binaries[0]), "synthetic modified");
    assert.throws(() => evidence.verifyPayload(app, target, hashes), /payload differs/);
  }
});
test("artifact identity binds filename, version, platform, size and SHA-256", t => {
  const f = fixture(t);
  for (const [platform, target] of Object.entries(evidence.TARGETS)) for (const extension of target.extensions) {
    const file = path.join(f.project, "dist", `MarkItDown-${f.pkg.version}-${target.os}-${target.arch}-unsigned.${extension}`); write(file, "synthetic artifact");
    const artifact = evidence.describeArtifact(file, platform, f.pkg.version);
    assert.equal(artifact.filename, path.basename(file)); assert.equal(artifact.bytes, 18);
    assert.equal(artifact.sha256, evidence.digest(Buffer.from("synthetic artifact"))); assert.equal(artifact.arch, target.arch);
    assert.throws(() => evidence.describeArtifact(file, platform, "0.0.0"), /filename/);
    assert.throws(() => evidence.describeArtifact(file, platform === "macos" ? "windows" : "macos", f.pkg.version), /filename/);
    write(file, "different artifact"); const changed = evidence.describeArtifact(file, platform, f.pkg.version);
    assert.equal(changed.bytes, artifact.bytes); assert.notEqual(changed.sha256, artifact.sha256);
    write(file, ""); assert.throws(() => evidence.describeArtifact(file, platform, f.pkg.version), /empty/);
  }
});
test("reports share exact CI run identity and exclude secrets and absolute paths", async t => {
  const f = fixture(t); const environment = { ...f.environment, HOME: "/private/home/user", GITHUB_TOKEN: "secret-auth-token", MARKITDOWN_SYNTHETIC_PASSWORD: "secret-password" };
  assert.deepEqual(evidence.ciIdentity(environment), f.snapshot.ci);
  const resources = evidence.verifyAsar(await archive(f), f.project, f.snapshot); const reports = [];
  for (const [platform, target] of Object.entries(evidence.TARGETS)) {
    const file = path.join(f.project, "dist", `MarkItDown-${f.pkg.version}-${target.os}-${target.arch}-unsigned.${target.extensions[0]}`); write(file, "synthetic installer fixture");
    const state = { snapshot: evidence.sourceSnapshot(f.root, environment), platform, started_at: "2026-10-08T00:00:00.000Z", completed_at: "2026-10-08T00:01:00.000Z", build_environment: { platform: target.host, architecture: target.arch, node: "24.19.0" } };
    const runtime = evidence.validateRuntime({ platform: target.host, arch: target.arch, electron: "44.6.0", node: "24.19.0", chrome: "150.0.0.0", v8: "15.0.1" }, state.snapshot, target);
    const report = evidence.createReport(state, evidence.describeArtifact(file, platform, f.pkg.version), resources, runtime, { status: "verified", matches_assembled_app: true }); reports.push(report);
    const text = JSON.stringify(report);
    for (const forbidden of [f.root, "/private/", "secret-auth-token", "secret-password", "HOME", "GITHUB_TOKEN"]) assert(!text.includes(forbidden));
    assert.equal(report.schema_version, 1); assert.equal(report.kind, "markitdown_desktop_build_evidence");
    for (const key of ["manual_install", "manual_first_launch", "manual_connect", "manual_save", "manual_cancel_save", "manual_overwrite"]) assert.equal(report.checks[key], "not_run");
    for (const key of ["signing", "notarization", "gatekeeper", "release"]) assert.equal(report.checks[key], "not_verified");
    assert.equal(report.checks.existing_native_service_verifier, "not_recorded_by_this_tool");
    assert.equal(report.versions.service_runtime.status, "not_measured"); assert.equal(report.versions.conversion_engines.status, "not_measured");
  }
  assert.deepEqual(reports[0].ci, reports[1].ci); assert.equal(reports[0].source.commit, reports[1].source.commit);
  assert.notEqual(reports[0].artifact.platform, reports[1].artifact.platform);
});
test("CI metadata rejects mismatched or path-shaped identities", () => {
  assert.deepEqual(evidence.ciIdentity({ PRIVATE_VALUE: "secret" }), { provider: "local" });
  const base = { GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: "owner/repo", GITHUB_RUN_ID: "123", GITHUB_RUN_ATTEMPT: "1", GITHUB_JOB: "desktop", GITHUB_SHA: "a".repeat(40) };
  for (const [key, value] of [["GITHUB_REPOSITORY", "/private/home"], ["GITHUB_JOB", "token=secret"], ["GITHUB_RUN_ID", "https://private.invalid"], ["GITHUB_SHA", "not-a-commit"]]) assert.throws(() => evidence.ciIdentity({ ...base, [key]: value }), /Invalid CI/);
});
