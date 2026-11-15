#!/bin/bash
# Assemble an Intel (x86_64) Harbor.app from this checkout, based on an Apple silicon
# Harbor.app of the SAME version (its Info.plist, icon, Documentation and build-info).
# service/ always comes from this checkout's commit (git archive), never from that bundle.
# No local engine: the result runs on a cloud model only. See docs/INTEL_BUILD.md.
#
# Required:
#   SRC_APP      the Apple silicon Harbor.app of the same version
#   OUT          output folder (replaced)
#   COMMIT       the commit of this checkout to package
#   PY_X86_TAR   x86_64 python-build-standalone "install_only" tarball, same Python as
#                build-support/toolchain.json
#   UV_X86_TAR   x86_64 uv release tarball (uv-x86_64-apple-darwin.tar.gz)
#   IDENTITY     SHA-1 of the code-signing identity that seals the app (a stable identity
#                keeps macOS privacy permissions and Keychain trust across updates)
# Optional:
#   BIN          the x86_64 HarborApp from scripts/intel/swift.sh
#                (default .harbor-build/swift-x86/release/HarborApp)
#   UV           a uv binary for this Mac (default: the build driver's .harbor-build/tools/uv)
#   UV_CACHE_DIR uv cache (default .harbor-build/uv-cache-x86)
#   VERSION, BUILD  override CFBundleShortVersionString / CFBundleVersion
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
: "${SRC_APP:?}" "${OUT:?}" "${COMMIT:?}" "${PY_X86_TAR:?}" "${UV_X86_TAR:?}" "${IDENTITY:?}"
BIN=${BIN:-$ROOT/.harbor-build/swift-x86/release/HarborApp}
UV=${UV:-$(find "$ROOT/.harbor-build/tools" -type f -name uv -perm +111 | head -1)}
export UV_CACHE_DIR=${UV_CACHE_DIR:-$ROOT/.harbor-build/uv-cache-x86}
lipo -archs "$BIN" | grep -qx x86_64
rm -rf "${OUT:?}"; mkdir -p "$OUT"
APP=$OUT/Harbor.app; R=$APP/Contents/Resources
mkdir -p "$R" "$APP/Contents/MacOS"
cp "$SRC_APP/Contents/Info.plist" "$APP/Contents/"
cp "$BIN" "$APP/Contents/MacOS/Harbor"
for n in AppIcon.icns Documentation assistant-task-corpus-v1.jsonl build-info.json; do cp -R "$SRC_APP/Contents/Resources/$n" "$R/"; done
# Release notes from this checkout's commit.
mkdir -p "$R/Documentation/releases"
git -C "$ROOT" archive "$COMMIT" docs/releases | tar -x -C "$OUT"
cp -R "$OUT/docs/releases/." "$R/Documentation/releases/" && rm -rf "$OUT/docs"
mkdir -p "$R/backend"
git -C "$ROOT" archive "$COMMIT" service | tar -x -C "$R/backend"
find "$R/backend/service" -name __pycache__ -type d -prune -exec rm -rf {} +
# The built-in Google sign-in client is written by the release pipeline, never kept in git.
G="$SRC_APP/Contents/Resources/backend/service/google_oauth_client.json"
if [ -f "$G" ]; then cp "$G" "$R/backend/service/"; else echo "note: no built-in Google client in SRC_APP"; fi
V=$R/backend/.venv; mkdir -p "$V"
tar -xzf "$PY_X86_TAR" -C "$V" --strip-components 1
rm -f "$V"/lib/python*/EXTERNALLY-MANAGED
PY=$V/bin/python3
"$UV" pip sync --python "$PY" --prefix "$V" --no-python-downloads \
  --require-hashes --only-binary :all: --strict --link-mode copy --default-index https://pypi.org/simple \
  --python-platform x86_64-apple-darwin "$ROOT/build-support/requirements-runtime.lock"
for e in "$V"/bin/*; do case "$(basename "$e")" in python|python3|python3.13) ;; *) rm -f "$e";; esac; done
[ -e "$V/bin/python" ] || ln -s python3 "$V/bin/python"
mkdir -p "$R/bin"; tar -xzf "$UV_X86_TAR" -C "$R/bin" --strip-components 1
/usr/bin/python3 - "$R/build-info.json" "$COMMIT" <<'PY'
import json, sys
p, c = sys.argv[1], sys.argv[2]; d = json.load(open(p))
d["commit"] = c; d["architecture"] = "x86_64"; d["preview"] = True
d.pop("engine", None); d.pop("engine_smoke", None)
if isinstance(d.get("uv"), dict):
    d["uv"]["asset"] = "uv-x86_64-apple-darwin.tar.gz"; d["uv"].pop("sha256", None)
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
PY
P="$APP/Contents/Info.plist"
if [ -n "${VERSION:-}" ]; then /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$P"; fi
if [ -n "${BUILD:-}" ]; then /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" "$P"; fi
/usr/libexec/PlistBuddy -c "Set :HarborSourceCommit $COMMIT" "$P"
/usr/libexec/PlistBuddy -c "Set :HarborPreviewBuild true" "$P"
/usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription Harbor listens only while you ask by voice (the mic or ⌥⇧Space) or while a meeting you started in Meet is recording. In this Intel build the recording is sent to the AI provider you connected to be turned into text; Harbor keeps no recording." "$P"
BAD=0
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    lipo -archs "$f" 2>/dev/null | grep -q x86_64 || { echo "NOT x86_64: $f"; BAD=1; }
    codesign --force --sign - --timestamp=none "$f" >/dev/null 2>&1 || { echo "SIGN FAIL $f"; BAD=1; }
  fi
done < <(find "$APP" -type f \( -perm +111 -o -name "*.so" -o -name "*.dylib" \))
# Nested Mach-Os stay ad-hoc; the app itself is sealed with $IDENTITY (by SHA-1, never by name).
codesign --force --sign "$IDENTITY" --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo SIGN_OK
REQ=$(codesign -d -r- "$APP" 2>&1 | sed -n 's/^designated => //p')
case "$REQ" in *'identifier "com.harbor.assistant"'*certificate*) echo "stable designated requirement";;
  *) echo "designated requirement is not stable: $REQ"; exit 3;; esac
[ $BAD = 0 ] && echo ARCH_OK
echo "VERSION $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$P") BUILD $(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$P")"
# service/ differences against the Apple silicon bundle (expected: only this branch's own changes).
diff -rq "$SRC_APP/Contents/Resources/backend/service" "$R/backend/service" -x __pycache__ \
  | sed 's#'"$SRC_APP"'#ARM#; s#'"$APP"'#INTEL#' > "$OUT/service-diff.txt" || true
echo "service differences: $(wc -l < "$OUT/service-diff.txt")"
du -sh "$APP"
[ $BAD = 0 ]
