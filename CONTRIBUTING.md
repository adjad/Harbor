# Contributing to Harbor

Thanks for helping. Harbor reads private data, so changes are held to a conservative standard:
nothing should send, delete or upload without the user's approval, and tests should use made-up data.

## Set up

You need Apple silicon, macOS 14 or newer, Python 3.13 (the version the app ships) and
Apple's Command Line Tools.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
./scripts/harbor-build doctor
```

## Build and run

```bash
./scripts/harbor-build preview    # fast local build; ZIP is written under dist/
./scripts/run.sh                  # backend only, http://127.0.0.1:8765
```

The app starts its own backend on port 8765, so stop `run.sh` before opening a built app.
`./scripts/harbor-build all` is the full release gate and needs a clean, committed tree.

## Run the tests

```bash
./scripts/harbor-build test       # Python tests and Swift contracts
./scripts/harbor-build swift      # native contracts and a release-mode Swift compile
```

Swift compiles are heavy. Run one build or test gate at a time on your machine; `--jobs N` limits
how many Simulation QA gates run at once (`--jobs 1` is sequential). [TESTING.md](TESTING.md) has the
manual test plan and seeded sample data. It seeds and clears only clearly tagged test items, but
read it before pointing anything at your real Calendar or Reminders.

If you add a Swift file, add it to the native compile lists in `build-support/` as well as the
package. If you add a test module, check that Simulation QA picks it up.

## Pull requests

- Keep each change to one purpose, and describe what it does and what you ran.
- Add or update a test. Use fictional people, schools and addresses, never real ones.
- Add a line under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in plain words, saying what
  the user sees and what was and was not tested.
- Do not commit keys, tokens, OAuth clients, debug exports or anything from `~/.moe`.
- Anything that touches permissions, outbound sends, stored data or what goes to a cloud provider
  needs a clear note on the privacy effect.

## Commit messages

Start with a short imperative sentence about the change ("Read the sender's real address, not the
display name"). Add a body when the reason is not obvious. One logical change per commit.

## Report a bug

Open an [issue](https://github.com/adjad/Harbor/issues/new/choose). In the app, right-click the
menu-bar icon and choose Report a Problem (or type `/report`) to build a report you save and attach
yourself; nothing is uploaded. Read it first and remove anything private. For a security problem,
see [SECURITY.md](SECURITY.md) instead.
