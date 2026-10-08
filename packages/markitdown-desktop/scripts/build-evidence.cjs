"use strict";
// Evidence for unsigned CI artifacts. This is an observation, not a signed attestation.
// Never read arbitrary environment variables, service responses, user files or logs.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const { isDeepStrictEqual } = require("node:util");

const PROJECT = "packages/markitdown-desktop";
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const SHA = /^[a-f0-9]{40}$/;
const TARGETS = {
  macos: {
    host: "darwin", arch: "arm64", os: "mac", extensions: ["dmg", "zip"],
    args: ["--mac", "--arm64", "--publish", "never"],
    app: "mac-arm64/MarkItDown.app",
    asar: "Contents/Resources/app.asar", executable: "Contents/MacOS/MarkItDown",
    binaries: ["Contents/MacOS/MarkItDown", "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"],
  },
  windows: {
    host: "win32", arch: "x64", os: "win", extensions: ["exe"],
    args: ["--win", "--x64", "--publish", "never"],
    app: "win-unpacked", asar: "resources/app.asar", executable: "MarkItDown.exe",
    binaries: ["MarkItDown.exe"],
  },
};

function requireThat(condition, message) {
  if (!condition) throw new Error(message);
}
function command(executable, args, options = {}) {
  try {
    return execFileSync(executable, args, { stdio: "pipe", timeout: 180000, maxBuffer: 16 * 1024 * 1024, ...options });
  } catch {
    // Child stderr can contain machine paths or credentials. Do not persist or echo it.
    throw new Error("Evidence command failed; inspect the corresponding build step locally.");
  }
}
function git(root, ...args) {
  return command("git", ["-C", root, ...args]).toString("utf8").trim();
}
function json(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }
function writeJson(file, value) { fs.writeFileSync(file, JSON.stringify(value, null, 2) + "\n", { flag: "wx" }); }
function digest(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function fileDigest(file) {
  const stat = fs.lstatSync(file);
  requireThat(stat.isFile() && !stat.isSymbolicLink(), "Expected a regular evidence input file.");
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(file, "r");
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    let length;
    while ((length = fs.readSync(fd, buffer, 0, buffer.length, null)) > 0) hash.update(buffer.subarray(0, length));
    const after = fs.fstatSync(fd);
    requireThat(stat.size === after.size && stat.mtimeMs === after.mtimeMs, "Evidence input changed while hashing.");
    return { bytes: stat.size, sha256: hash.digest("hex") };
  } finally { fs.closeSync(fd); }
}
function checkedVersion(value) {
  requireThat(typeof value === "string" && VERSION.test(value), "Expected an exact version, not a range or private value.");
  return value;
}
function targetFor(platform) {
  requireThat(Object.hasOwn(TARGETS, platform), "Supported evidence targets are macos and windows.");
  return TARGETS[platform];
}
function ciIdentity(environment = process.env) {
  if (environment.GITHUB_ACTIONS !== "true") return { provider: "local" };
  const { GITHUB_REPOSITORY: repository, GITHUB_RUN_ID: run_id, GITHUB_RUN_ATTEMPT: run_attempt, GITHUB_JOB: job, GITHUB_SHA: commit } = environment;
  requireThat(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository || ""), "Invalid CI repository.");
  requireThat(/^[0-9]+$/.test(run_id || "") && /^[0-9]+$/.test(run_attempt || ""), "Invalid CI run identity.");
  requireThat(/^[A-Za-z0-9_-]+$/.test(job || "") && SHA.test(commit || ""), "Invalid CI job or commit.");
  return { provider: "github_actions", repository, run_id, run_attempt, job, commit };
}
function pythonVersion(root, relative, variable) {
  const source = fs.readFileSync(path.join(root, relative), "utf8");
  const match = source.match(new RegExp(`^${variable}\\s*=\\s*["']([^"']+)["']`, "m"));
  requireThat(match, "Missing declared Python package version.");
  return checkedVersion(match[1]);
}
function sourceSnapshot(root, environment = process.env) {
  requireThat(git(root, "status", "--porcelain=v1", "--untracked-files=all") === "", "Source must be clean, including staged and untracked files.");
  // Do not let assume-unchanged/skip-worktree hide changes from the clean check.
  requireThat(git(root, "ls-files", "-v").split("\n").every(line => line.startsWith("H ")), "Source index has hidden or unsupported entries.");
  const commit = git(root, "rev-parse", "HEAD");
  const tree = git(root, "rev-parse", "HEAD^{tree}");
  requireThat(SHA.test(commit) && SHA.test(tree), "Invalid source commit or tree.");
  const ci = ciIdentity(environment);
  requireThat(ci.provider !== "github_actions" || ci.commit === commit, "Checked-out source does not match the CI commit.");
  // Hash every tracked file rather than trusting Git's mtime/size cache. This also
  // detects a same-size edit whose timestamp was restored before inspection.
  const treeEntries = git(root, "ls-tree", "-r", "--full-tree", "-z", "HEAD").split("\0").filter(Boolean).map(entry => {
    const match = entry.match(/^(100644|100755) blob ([a-f0-9]{40})\t([^\0]+)$/);
    requireThat(match && !/[\r\n]/.test(match[3]), "Unsupported tracked source entry.");
    return { hash: match[2], path: match[3] };
  });
  const hashes = command("git", ["-C", root, "hash-object", "--stdin-paths"], {
    input: treeEntries.map(entry => JSON.stringify(entry.path)).join("\n") + "\n",
  }).toString("utf8").trim().split("\n");
  requireThat(isDeepStrictEqual(hashes, treeEntries.map(entry => entry.hash)), "Tracked source bytes differ from the source commit.");
  const project = path.join(root, PROJECT);
  const pkg = json(path.join(project, "package.json"));
  const lock = json(path.join(project, "package-lock.json"));
  requireThat(lock.version === pkg.version && lock.packages[""].version === pkg.version, "Source and lockfile versions differ.");
  requireThat(pkg.name === "markitdown-desktop" && pkg.main === "src/main.cjs" && pkg.build.asar === true,
    "Unexpected desktop identity or packaging mode.");
  requireThat(pkg.build.artifactName === "MarkItDown-${version}-${os}-${arch}-unsigned.${ext}", "Unexpected installer naming policy.");
  requireThat(!pkg.dependencies || Object.keys(pkg.dependencies).length === 0, "Runtime dependencies need explicit evidence coverage.");
  const versions = {
    desktop: checkedVersion(pkg.version), electron_declared: checkedVersion(pkg.devDependencies.electron),
    electron_builder_declared: checkedVersion(pkg.devDependencies["electron-builder"]),
    asar_declared: checkedVersion(pkg.devDependencies["@electron/asar"]),
    service_declared: pythonVersion(root, "packages/markitdown-web/src/markitdown_web/__init__.py", "__version__"),
    core_declared: pythonVersion(root, "packages/markitdown/src/markitdown/__about__.py", "__version__"),
  };
  requireThat(versions.service_declared === pythonVersion(root, "packages/markitdown-web/pyproject.toml", "version"), "Service source versions differ.");
  const runtimeFiles = git(root, "ls-files", "-z", "--", `${PROJECT}/src`, `${PROJECT}/LICENSE`).split("\0").filter(Boolean).sort();
  requireThat(runtimeFiles.includes(`${PROJECT}/src/main.cjs`) && runtimeFiles.includes(`${PROJECT}/LICENSE`), "Missing committed runtime resources.");
  const resources = runtimeFiles.map(relative => {
    requireThat(/^[A-Za-z0-9_./-]+$/.test(relative) && !relative.split("/").includes(".."), "Unsafe runtime resource name.");
    const file = path.join(root, relative);
    // Git's clean filters account for an ordinary Windows CRLF checkout.
    requireThat(git(root, "hash-object", `--path=${relative}`, file) === git(root, "rev-parse", `HEAD:${relative}`), "Runtime source differs from the source commit.");
    return { path: relative.slice(PROJECT.length + 1), ...fileDigest(file) };
  });
  return {
    source: { commit, tree, clean: true }, ci, versions, resources,
    inputs: { package_json: fileDigest(path.join(project, "package.json")), package_lock: fileDigest(path.join(project, "package-lock.json")) },
  };
}
function assertSameSource(before, after) {
  requireThat(isDeepStrictEqual(before, after), "Source, version, or CI identity changed during the build.");
}
function verifyAsar(archive, project, snapshot) {
  const asar = require("@electron/asar");
  asar.uncache(archive);
  const entries = asar.listPackage(archive).map(value => value.replace(/\\/g, "/").replace(/^\//, ""));
  const files = [];
  for (const entry of entries) {
    requireThat(!entry.split("/").includes("..") && !entry.startsWith("/"), "Unsafe packaged resource path.");
    const stat = asar.statFile(archive, entry, false);
    requireThat(!stat.link && !stat.unpacked, "Linked or unpacked runtime resources are not covered.");
    if (!stat.files) files.push(entry);
  }
  requireThat(isDeepStrictEqual(files.sort(), [...snapshot.resources.map(resource => resource.path), "package.json"].sort()), "Missing or unexpected packaged runtime resources.");
  for (const resource of snapshot.resources) {
    const data = asar.extractFile(archive, resource.path);
    requireThat(data.length === resource.bytes && digest(data) === resource.sha256, "Packaged runtime bytes differ from clean source.");
  }
  const packaged = JSON.parse(asar.extractFile(archive, "package.json").toString("utf8"));
  const expected = json(path.join(project, "package.json"));
  // The pinned builder removes these build-only properties. Compare every remaining
  // field, so a changed main/type/imports/dependency cannot hide behind a version match.
  for (const key of ["build", "scripts", "devDependencies", "keywords"]) delete expected[key];
  requireThat(isDeepStrictEqual(packaged, expected), "Packaged package.json differs from source runtime metadata.");
  return { status: "verified", scope: "all_app_asar_files", archive: fileDigest(archive), resources: snapshot.resources, desktop_version: packaged.version };
}
function validateRuntime(value, snapshot, target) {
  requireThat(value && value.platform === target.host && value.arch === target.arch, "Packaged executable platform or architecture differs from the target.");
  for (const key of ["electron", "node", "chrome", "v8"]) {
    requireThat(typeof value[key] === "string" && /^[0-9][0-9A-Za-z.+-]{0,79}$/.test(value[key]), "Invalid executed runtime version.");
  }
  requireThat(value.electron === snapshot.versions.electron_declared, "Executed Electron differs from the declared version.");
  return { status: "executed", method: "packaged_executable_node_mode", platform: value.platform, arch: value.arch,
    electron: value.electron, node: value.node, chromium: value.chrome, v8: value.v8 };
}
function probeRuntime(app, target, snapshot) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "markitdown-runtime-"));
  try {
    const out = path.join(temporary, "versions.json");
    const code = 'const v=process.versions;require("node:fs").writeFileSync(process.argv[1],JSON.stringify({platform:process.platform,arch:process.arch,electron:v.electron,node:v.node,chrome:v.chrome,v8:v.v8}),{flag:"wx"})';
    command(path.join(app, target.executable), ["-e", code, out], { env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" }, timeout: 30000 });
    return validateRuntime(json(out), snapshot, target);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}
function verifyPayload(app, target, expected) {
  const resources = [target.asar, ...target.binaries].map(relative => ({ path: relative, ...fileDigest(path.join(app, relative)) }));
  if (expected) requireThat(isDeepStrictEqual(resources, expected), "Installer payload differs from the verified assembled app.");
  return resources;
}
function inspectInstaller(artifact, target, expected) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "markitdown-payload-"));
  let mounted = false;
  const mount = path.join(temporary, "volume");
  try {
    let app;
    if (artifact.endsWith(".dmg")) {
      fs.mkdirSync(mount);
      command("hdiutil", ["attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", mount, artifact]);
      mounted = true;
      app = path.join(mount, "MarkItDown.app");
    } else if (artifact.endsWith(".zip")) {
      command("ditto", ["-x", "-k", artifact, temporary]);
      app = path.join(temporary, "MarkItDown.app");
    } else {
      // Existing GitHub Windows runner tool. Never run the installer or install tools.
      const sevenZip = path.join(process.env.ProgramFiles || "C:\\Program Files", "7-Zip", "7z.exe");
      requireThat(fs.existsSync(sevenZip), "The Windows runner needs its existing 7-Zip tool to inspect NSIS payloads.");
      command(sevenZip, ["x", artifact, `-o${temporary}`, "-y", "$PLUGINSDIR\\app-64.7z"]);
      const payload = path.join(temporary, "$PLUGINSDIR", "app-64.7z");
      requireThat(fs.existsSync(payload), "NSIS installer has no expected x64 payload archive.");
      app = path.join(temporary, "app");
      command(sevenZip, ["x", payload, `-o${app}`, "-y", ...[target.asar, ...target.binaries].map(relative => relative.replaceAll("/", "\\"))]);
    }
    verifyPayload(app, target, expected);
    return { status: "verified", method: artifact.endsWith(".dmg") ? "read_only_dmg_mount" : "archive_extraction",
      scope: "app_asar_and_runtime_binaries", matches_assembled_app: true, files: expected };
  } finally {
    try { if (mounted) command("hdiutil", ["detach", mount], { timeout: 60000 }); }
    finally { if (!mounted || !fs.existsSync(path.join(mount, "MarkItDown.app"))) fs.rmSync(temporary, { recursive: true, force: true }); }
  }
}
function describeArtifact(file, platform, version) {
  const target = targetFor(platform);
  const extension = path.extname(file).slice(1);
  const filename = `MarkItDown-${checkedVersion(version)}-${target.os}-${target.arch}-unsigned.${extension}`;
  requireThat(target.extensions.includes(extension) && path.basename(file) === filename, "Installer filename, version or platform does not match this build.");
  const detail = fileDigest(file);
  requireThat(detail.bytes > 0, "Installer is empty.");
  return { filename, version, platform, arch: target.arch, ...detail };
}
function installedTools(project, snapshot) {
  const read = name => checkedVersion(json(path.join(project, "node_modules", name, "package.json")).version);
  const result = { electron_package: read("electron"), electron_builder: read("electron-builder"), asar: read("@electron/asar") };
  requireThat(result.electron_package === snapshot.versions.electron_declared && result.electron_builder === snapshot.versions.electron_builder_declared && result.asar === snapshot.versions.asar_declared,
    "Installed build tools differ from the pinned declarations.");
  return result;
}
function buildEnvironment(project, snapshot) {
  return { platform: process.platform, architecture: process.arch, os_release: os.release(), node: process.versions.node,
    tools: installedTools(project, snapshot) };
}
function createReport(state, artifact, resources, runtime, payload) {
  return {
    schema_version: 1, kind: "markitdown_desktop_build_evidence",
    generated_at: new Date().toISOString(), source: state.snapshot.source, ci: state.snapshot.ci, artifact,
    build: { status: "passed", started_at: state.started_at, completed_at: state.completed_at, command: ["electron-builder", ...targetFor(state.platform).args] },
    build_environment: state.build_environment, source_inputs: state.snapshot.inputs,
    versions: { ...state.snapshot.versions, desktop_packaged: resources.desktop_version, desktop_runtime: runtime,
      service_runtime: { status: "not_measured", reason: "Remote service is not bundled in this desktop installer." },
      conversion_engines: { status: "not_measured", reason: "Declared service/core versions do not establish executed conversion engine versions." } },
    packaged_resources: resources, installer_payload: payload,
    checks: {
      clean_source_before_and_after: "passed", packaged_resources_match_source: "passed", installer_payload_matches_assembled_app: "passed",
      packaged_runtime_versions: "passed", manual_install: "not_run", manual_first_launch: "not_run", manual_connect: "not_run",
      manual_save: "not_run", manual_cancel_save: "not_run", manual_overwrite: "not_run",
      signing: "not_verified", notarization: "not_verified", gatekeeper: "not_verified", release: "not_verified",
      existing_native_service_verifier: "not_recorded_by_this_tool",
    },
    limitations: ["Unsigned observational build evidence; not a cryptographic provenance attestation.",
      "source.commit is the actual clean checkout, including a GitHub merge-ref commit for pull-request jobs; it is not inferred from a branch head.",
      "Archive inspection and an Electron Node-mode probe do not establish GUI, installation, save, overwrite, signing, notarization or release acceptance.",
      "Existing service and GUI verifiers have separate workflow results; this report does not infer their outcomes.",
      "Payload comparison covers app.asar and listed runtime binaries, not every OS resource in the installer."],
  };
}
function runBuild(root, platform) {
  const target = targetFor(platform);
  requireThat(process.platform === target.host, "Build evidence must run on the native target platform.");
  const project = path.join(root, PROJECT);
  const dist = path.join(project, "dist");
  requireThat(!fs.existsSync(dist) || fs.readdirSync(dist).length === 0, "Use a fresh output directory; stale build output cannot acquire new evidence.");
  const snapshot = sourceSnapshot(root);
  const state = { schema_version: 1, platform, snapshot, started_at: new Date().toISOString(), build_environment: buildEnvironment(project, snapshot) };
  fs.mkdirSync(dist, { recursive: true });
  writeJson(path.join(dist, "build-source.json"), state);
  command(process.execPath, [path.join(project, "node_modules/electron-builder/cli.js"), ...target.args], { cwd: project, stdio: "inherit", timeout: 15 * 60 * 1000 });
  assertSameSource(snapshot, sourceSnapshot(root));
  state.completed_at = new Date().toISOString();
  writeJson(path.join(dist, "build-completed.json"), state);
}
function finalize(root) {
  const project = path.join(root, PROJECT);
  const dist = path.join(project, "dist");
  const state = json(path.join(dist, "build-completed.json"));
  requireThat(state.schema_version === 1 && state.completed_at && state.started_at, "No completed evidence build is available.");
  const target = targetFor(state.platform);
  requireThat(process.platform === target.host, "Evidence inspection must run on the native target platform.");
  assertSameSource(state.snapshot, sourceSnapshot(root));
  requireThat(isDeepStrictEqual(state.build_environment, buildEnvironment(project, state.snapshot)), "Build environment changed before evidence collection.");
  const app = path.join(dist, target.app);
  const resources = verifyAsar(path.join(app, target.asar), project, state.snapshot);
  const payloadHashes = verifyPayload(app, target);
  const runtime = probeRuntime(app, target, state.snapshot);
  const expectedNames = target.extensions.map(extension => `MarkItDown-${state.snapshot.versions.desktop}-${target.os}-${target.arch}-unsigned.${extension}`);
  const actualNames = fs.readdirSync(dist).filter(name => /\.(dmg|zip|exe)$/i.test(name)).sort();
  requireThat(isDeepStrictEqual(actualNames, [...expectedNames].sort()), "Missing, stale or unexpected installer files in the build output.");
  const reports = expectedNames.map(filename => {
    const file = path.join(dist, filename);
    const artifact = describeArtifact(file, state.platform, state.snapshot.versions.desktop);
    const payload = inspectInstaller(file, target, payloadHashes);
    requireThat(isDeepStrictEqual(artifact, describeArtifact(file, state.platform, state.snapshot.versions.desktop)), "Installer changed while collecting evidence.");
    return createReport(state, artifact, resources, runtime, payload);
  });
  assertSameSource(state.snapshot, sourceSnapshot(root));
  verifyPayload(app, target, payloadHashes);
  for (const report of reports) writeJson(path.join(dist, `${report.artifact.filename}.build-evidence.json`), report);
  console.log(`Verified and wrote ${reports.length} unsigned installer evidence report(s).`);
}
if (require.main === module) {
  try {
    const root = path.resolve(__dirname, "../../..");
    if (process.argv[2] === "build" && process.argv.length === 4) runBuild(root, process.argv[3]);
    else if (process.argv[2] === "finalize" && process.argv.length === 3) finalize(root);
    else throw new Error("Usage: build-evidence.cjs build macos|windows, or build-evidence.cjs finalize");
  } catch (error) {
    // FS/JSON errors may include private absolute input paths. Only our bounded errors are safe.
    const safe = error.code || error instanceof SyntaxError ? "Evidence input could not be read or parsed." : error.message;
    console.error(safe);
    process.exitCode = 1;
  }
}
module.exports = { sourceSnapshot, assertSameSource, verifyAsar, validateRuntime, verifyPayload, describeArtifact, ciIdentity, createReport, TARGETS, digest, fileDigest };
