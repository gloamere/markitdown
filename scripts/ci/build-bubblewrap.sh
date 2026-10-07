#!/usr/bin/env bash
# Build an official checksum-pinned non-setuid launcher into this disposable
# runner's temporary directory. No host security settings or system binaries change.
set -euo pipefail
ROOT="${RUNNER_TEMP:?A provisioned disposable runner is required}/markitdown-bubblewrap-0.13.0"
PREFIX="$ROOT/install"
URL=https://github.com/containers/bubblewrap/releases/download/v0.13.0/bubblewrap-0.13.0.tar.xz
SHA256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765
mkdir -p "$ROOT/source"
chmod 700 "$ROOT"
curl --proto '=https' --tlsv1.2 --fail --location --retry 2 --max-time 120 \
  "$URL" --output "$ROOT/source.tar.xz"
printf '%s  %s\n' "$SHA256" "$ROOT/source.tar.xz" | sha256sum --check --strict
tar --extract --xz --file "$ROOT/source.tar.xz" --strip-components=1 --directory "$ROOT/source"
meson setup "$ROOT/build" "$ROOT/source" --prefix "$PREFIX" \
  --buildtype=release \
  -Dbash_completion_dir="$PREFIX/share/bash-completion/completions" \
  -Dzsh_completion_dir="$PREFIX/share/zsh/site-functions"
meson compile -C "$ROOT/build"
meson install -C "$ROOT/build"
"$PREFIX/bin/bwrap" --version | grep -Fx 'bubblewrap 0.13.0'
# The official release removed setuid support; also verify installed mode.
test ! -u "$PREFIX/bin/bwrap"
test ! -g "$PREFIX/bin/bwrap"
python3 - "$PREFIX/bin/bwrap" "$URL" "$SHA256" "$ROOT/provenance.json" <<'PY'
import hashlib, json, pathlib, stat, sys
binary, source, checksum, output = sys.argv[1:]
p = pathlib.Path(binary)
mode = p.stat().st_mode
assert not mode & (stat.S_ISUID | stat.S_ISGID)
record = {'version':'0.13.0','source_url':source,'source_sha256':checksum,
          'release_commit':'719a4fd474d44b26906bcf2b1b0fb6eddd8d56d0',
          'binary_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
          'non_setuid':True,'host_security_settings_changed':False}
pathlib.Path(output).write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record))
PY
printf 'MARKITDOWN_SANDBOX_BWRAP=%s\n' "$PREFIX/bin/bwrap" >> "${GITHUB_ENV:?CI environment output required}"
