"use strict";
// Reuse a named existing keychain identity. No export, import or auto-notarize.
const { build, Platform, Arch } = require("electron-builder");
const { execFileSync, spawnSync } = require("node:child_process");
const path = require("node:path");

async function run() {
  if (process.platform !== "darwin") throw new Error("Developer ID builds require macOS.");
  if (process.env.CSC_LINK || process.env.CSC_KEY_PASSWORD) throw new Error("This local-keychain build refuses certificate import credentials. Remove CSC_LINK and CSC_KEY_PASSWORD from its environment.");
  const identity = process.env.CSC_NAME;
  if (!identity || !/^Developer ID Application: .+ \([A-Z0-9]{10}\)$/.test(identity)) throw new Error("Set CSC_NAME to an existing Developer ID Application identity. Do not supply a password.");
  const identities = execFileSync("security", ["find-identity", "-v", "-p", "codesigning"], { encoding: "utf8" });
  if (!identities.includes(`"${identity}"`)) throw new Error("The requested identity is not a valid keychain code-signing identity.");
  const root = path.resolve(__dirname, "..");
  await build({ projectDir: root, targets: Platform.MAC.createTarget(["dmg", "zip"], Arch.arm64), publish: "never", config: {
    directories: { output: "dist/developer-id" },
    artifactName: "MarkItDown-${version}-${os}-${arch}-developer-id-unnotarized.${ext}",
    extraMetadata: { markitdownDistribution: "developer-id-unnotarized" },
    forceCodeSigning: true,
    mac: { identity: identity.slice("Developer ID Application: ".length), hardenedRuntime: true, notarize: false, strictVerify: true, preAutoEntitlements: false,
      entitlements: "signing/entitlements.mac.plist", entitlementsInherit: "signing/entitlements.mac.plist" }
  } });
  const app = path.join(root, "dist/developer-id/mac-arm64/MarkItDown.app");
  execFileSync("codesign", ["--verify", "--deep", "--strict", "--verbose=2", app], { stdio: "inherit" });
  const signature = spawnSync("codesign", ["--display", "--verbose=4", app], { encoding: "utf8" });
  if (signature.status !== 0 || !signature.stderr.includes(`Authority=${identity}`) || !signature.stderr.includes("runtime")) throw new Error("Expected the named Developer ID authority and hardened runtime.");
  console.log("Developer ID signing verified. Notarization and Gatekeeper distribution acceptance have NOT been completed.");
}
run().catch((error) => { console.error(error.message); process.exit(1); });
