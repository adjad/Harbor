# Building Harbor for Intel Macs

The Intel build is a preview. It has no local engine: every model role runs on the cloud
provider you connect (OpenRouter or the Gemini API, see [REMOTE_INFERENCE.md](REMOTE_INFERENCE.md)).
Your mail, messages, calendar, tools and approvals stay on the Mac.

It is not made by `./scripts/harbor-build`. It is put together from an Apple silicon build of the
same version by three scripts in `scripts/intel/`.

## What you need

- An Apple silicon `Harbor.app` of the **same version**, built with `./scripts/harbor-build preview`
  (or `all`). Its Info.plist, icon, documentation and build info are reused.
- Xcode or Command Line Tools able to build Swift for `x86_64` (`swift build --arch x86_64`).
- The x86_64 Python runtime: a python-build-standalone `install_only` tarball for
  `x86_64-apple-darwin`, the same Python version as `build-support/toolchain.json`.
- The x86_64 `uv` release tarball (`uv-x86_64-apple-darwin.tar.gz`) for the bundled `uv`.
- A code-signing identity in your keychain, passed by its SHA-1. A self-signed identity works for
  local testing. Keep using the same one so macOS keeps Harbor's privacy permissions and Keychain
  trust across updates. With an empty `IDENTITY` the app is ad-hoc signed: it runs, but macOS asks for
  its permissions again after every update.

## Steps

```bash
scripts/intel/stage.sh     # copy app/Sources to .harbor-build/swift-source-intel, drop the dev-checkout fallback
scripts/intel/swift.sh     # swift build -c release --arch x86_64 (heavy: run one at a time)
SRC_APP=dist/<arm64-preview>/Harbor.app OUT=dist/intel COMMIT=$(git rev-parse HEAD) \
PY_X86_TAR=<python tarball> UV_X86_TAR=<uv tarball> IDENTITY=<sha-1> \
  scripts/intel/assemble.sh
```

`assemble.sh` copies `service/` from the given commit, installs the pinned runtime dependencies for
x86_64 from `build-support/requirements-runtime.lock`, checks that every Mach-O is x86_64, signs
nested code ad-hoc, seals the app with your identity, and writes `service-diff.txt` listing how its
`service/` differs from the Apple silicon bundle.

The result is ad-hoc signed and not notarized. Open it the same way as any source build (see the
README).
