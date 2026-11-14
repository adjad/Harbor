<p align="center">
  <img src="docs/assets/harbor-wordmark.svg" alt="Harbor" width="320">
</p>

Harbor is a private assistant for the Mac. It lives in the notch (or the menu bar on a Mac without
one), reads your mail, messages, calendar and reminders on the Mac, and works out what needs you
today. The AI that does the reading is a local Gemma model running on Apple silicon, so by default
none of that content leaves the computer. Cloud features exist, but each one is opt-in and says what
it sends.

Harbor is a Swift app plus a bundled Python (FastAPI) service. It is Apache-2.0 licensed.

## Features

- **Ask.** Type or speak a request. Harbor looks through mail, messages, calendar, reminders and
  notes, searches the web when it needs to, and can act: draft mail and texts, add events and
  reminders, open apps. Sending, calendar changes and other risky actions wait for your approval.
  Smart Search (Cmd-Shift-F) answers a question about the document on your screen and shows the
  passage it came from.
- **Brief.** A summary of what needs you, refreshed a few times a day: who is waiting on a reply
  (with a draft ready), deadlines and bills found in mail, and today's to-dos from every source.
  Each item is a card you can act on or dismiss.
- **Day.** A time-ordered agenda of meetings, reminders and open time, plus school deadlines and
  suggested study blocks. A study block stays in Harbor until you tap "Add to calendar" on it; that
  tap books exactly that block in your calendar, and Undo removes it again.
- **Study.** Reads coursework from Canvas pages you have open in your browser (through the
  Accessibility API; Harbor never clicks, submits or types on a school site) and from Google Docs
  and Slides you pick in Google Drive. It makes study guides, practice quizzes, short lessons and
  flash cards. Study material is written by a cloud model you connect with your own key. Drive
  slides and PDFs are read one page at a time with Gemini. Brief then shows "Ready for tomorrow"
  with a prepared quiz, Day shows the slides that match a deadline and a "Review (15 min)" item,
  and Ask answers cite the deck and page.

Also in the app: meeting notes, reading files attached to email, and an optional mode where Harbor
adds events and reminders by itself from trusted sources (off until you say yes, with an Undo on
each action). See [CHANGELOG.md](CHANGELOG.md).

## How it uses Gemini and Google APIs

Local Gemma is the default. Ask, Brief and Day run on it with no cloud account. Gemini is used only
where you connect it.

- **Gemini API as a cloud provider.** Harbor can send chat roles to the Gemini API
  (`generativelanguage.googleapis.com`, OpenAI-compatible endpoint) with a key you store in the
  macOS Keychain. Tool calls use function calling. On the Gemini API Harbor keeps each call's
  thought signature and sends it back unchanged on the next request of the same turn, as Google
  requires, then drops it when the turn ends. Signatures are never logged, shown or stored in
  history. Only the exact Gemini origin is accepted for a Gemini key.
- **Gemini for everything.** On Apple silicon, a switch in Settings > Advanced lets the Gemini API
  handle every role. While it is on, pictures and voice recordings also go to Gemini.
- **Study.** Study has its own connector: OpenRouter or the Gemini API, with a key used for Study and
  nothing else. On the Gemini API each kind of material, and page extraction, has its own response
  schema; other providers fall back to JSON mode. Harbor validates the result strictly before it
  shows a quiz, flash cards, a lesson or a guide.
- **Long context.** When the Study model is a Gemini model, Harbor reads more pages per import in
  parallel and sends much larger blocks of source text, so a guide can be written from a whole
  course at once instead of one page at a time. The bounds are in
  [docs/STUDY_TAB.md](docs/STUDY_TAB.md).
- **Gmail, Calendar and Drive through Google sign-in.** One sign-in per Google account asks for
  read-only scopes: Gmail (required), and optionally Calendar, Drive, Docs, Sheets, Slides, Chat,
  Tasks and Contacts. Harbor cannot send, edit or delete anything in your Google account. Brief and
  Day use Gmail and Calendar; Study and Ask use Drive and Docs. See
  [docs/google-cloud-setup.md](docs/google-cloud-setup.md) for the exact scopes and APIs.

## Privacy

What stays on the Mac by default:

- Mail, messages, calendar, reminders, notes, and contacts are read on the Mac and stored in `~/.moe`.
- The chat model, indexes and caches run and live on the Mac. Harbor's own engine is built in.
- Every action Harbor takes is appended to `~/.moe/audit.jsonl`.
- "Report a Problem" builds a file you save yourself. Nothing is uploaded.

What can leave the Mac, only after you turn it on:

- **A cloud chat model.** If you connect one in Settings > Models > Cloud (or run the cloud-only
  build), the prompts, conversation context and tool schemas for the roles you moved go to that
  provider, and it may charge you.
- **Study.** Page text from your courses and the text of Drive files you add are sent to the Study
  model when you ask for material. Without a Study key nothing is sent.
- **Google.** Harbor talks to Google only for accounts you sign in. It reads; it does not write.
- **Web search and research,** when you ask a question that needs them.

Text from mail, web pages, school pages and files is treated as untrusted data, never as
instructions. Some actions always need your approval whatever the setting: sending mail or messages,
calendar changes, AppleScript, permanent deletes, installing a tool.

To turn cloud use off:

- Settings > Models > Cloud: disconnect the provider.
- Study: use Remove key on the Study key card. Keys are in the macOS Keychain, not in files.
- Settings > Google > Disconnect & erase removes the account and everything Harbor read from it.
- `/resetharbor` deletes everything Harbor stored and revokes its permissions.

## Requirements

- Apple silicon (M1 or later) for the local model, 16 GB of memory, about 8 GB free disk for the
  first model download.
- macOS 26.2 or newer to use the built-in engine. On macOS 14 to 26.1, Harbor needs another local
  engine running (oMLX, Ollama, LM Studio or any OpenAI-compatible server on the Mac).
- Building from source needs macOS 14 or newer, Python 3.13 (the version the app ships; the build driver itself starts on Python 3.9 or newer and fetches its own pinned 3.13 runtime), and Apple's Command Line Tools.
- Intel Macs cannot run the local model. A cloud-only mode sends every model role to one
  OpenAI-compatible server you configure while your data, tools and approvals stay on the Mac. See
  [docs/REMOTE_INFERENCE.md](docs/REMOTE_INFERENCE.md).

## Install and first run

There are no published releases yet, so build from source (next section). Builds are ad-hoc signed,
not notarized, so macOS blocks the first open:

1. Unzip the build and move `Harbor.app` to Applications.
2. Open it. When macOS says it cannot verify the app, choose Done.
3. Open System Settings > Privacy & Security, scroll down, and choose Open Anyway.

Setup is one window with five skippable steps: the model (it downloads in the background), macOS
permissions (each optional, each asked only when you click Allow), Google (optional), a few things
about you (optional), and a short tour. Harbor then lives in the notch and menu bar. Option-Space
opens Ask, Option-Shift-Space asks by voice, Command-5 opens Study. To quit, right-click the
menu-bar icon.

## Build from source

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

./scripts/harbor-build doctor      # check the toolchain
./scripts/harbor-build preview     # local build, writes a ZIP under dist/
./scripts/harbor-build test        # Python tests plus Swift contracts
```

`preview` is the fast path: it builds and signs the app and runs structure checks, but skips
Simulation QA, and its output is marked as a preview and is not for distribution.
`./scripts/harbor-build all` runs the full gate on a clean, committed tree. Details are in
[docs/build-release.md](docs/build-release.md) and [TESTING.md](TESTING.md).

To run only the backend while developing, start a local model server, then:

```bash
./scripts/run.sh     # serves http://127.0.0.1:8765
```

Google sign-in in a source build needs your own OAuth client; see
[docs/google-cloud-setup.md](docs/google-cloud-setup.md).

## Project layout

- `app/`: the Swift app (notch UI, settings, setup, permissions).
- `service/`: the Python agent service: tools, assistant layer, memory, inference providers, Study.
- `scripts/` and `build-support/`: the build driver, test and evaluation scripts.
- `tests/`, `test_fixtures/`: Python tests and Swift contract checks.
- `browser-extension/`: Chrome and Safari extension sources.
- `docs/`: architecture notes, the Study tab spec, inference providers, build and release.

## Status and known limits

Harbor is pre-1.0 and changes quickly. Be aware of the following.

- Not notarized. Harbor does not update itself.
- The cloud-key fixes in 0.5.2 are covered by tests that stand in for the Keychain. They have not
  been tried by hand with a real key and real macOS prompts.
- Study is new and unreleased. Treat it as untested against real school sites and real Drive files
  until you have tried it yourself.
- Several recent features (reading email and Messages attachments, meeting notes, writing-style
  drafts) are noted in the changelog as not yet tried in the running app.
- Google sign-in in the shared test client expires every 7 days and shows an "unverified app"
  warning, because the client has not been through Google's verification.
- Study reads Canvas first; other school sites are not supported.
- Reading the Mac's Mail and Messages needs Full Disk Access for some cases.

Bugs go in [issues](https://github.com/adjad/Harbor/issues). See [CONTRIBUTING.md](CONTRIBUTING.md)
and [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
