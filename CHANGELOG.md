# Changelog

All notable changes to Harbor are documented here.

## [Unreleased]

## [0.1.0] - 2026-11-14

First public release. See [docs/releases/0.1.0.md](docs/releases/0.1.0.md) for the release notes.

### Added
- **Ask.** Chat with tools over mail, messages, calendar, reminders, notes and the web; drafts, events, reminders and opening apps, with risky actions waiting for approval; voice; Smart Search (Command-Shift-F); answers about course slides cite the deck and page.
- **Brief.** A summary of what needs you, refreshed a few times a day, as cards you can act on or dismiss; a school row; "Ready for tomorrow" for a quiz or exam with matching slides; weak spots; a school item never shows twice across Brief and Day; an optional "Let Harbor act for me" switch, off until you say yes, with an Undo on each action.
- **Day.** A time-ordered agenda with school deadlines, suggested study blocks, the slides that cover each deadline and a "Review (15 min)" item; "Add to calendar" books a study block in your calendar, and Undo removes it again.
- **Study.** Courses and assignments read from open Canvas pages, read-only, with duplicate course rows merged; Google Drive Docs, Slides and PDFs as sources; study guides, practice quizzes, short lessons and flash cards written by a cloud model with a key used for Study only; slides and PDFs read page by page on the Gemini API, with reading and failed states and Try again.
- **Find course materials.** Harbor opens only a course's own Pages, Discussions and section list pages in your signed-in browser, read-only, up to 100 pages a run with a pause between opens. It never opens tests, quizzes, surveys, graded or proctored work, submission or attempt pages, payments, meetings or outside tools, and lists each refused item with the reason. The Study model decides link by link which outside links and public PDFs are safe to read, fetched without cookies or sign-in; nothing is added until you tick it.
- **Gemini and Google.** The Gemini API as a cloud provider; a "Gemini handles everything" switch in Settings > Advanced that also sends pictures and voice to Gemini; response schemas for Study material; whole-course guides with long context; read-only Gmail, Calendar and Drive through Google sign-in.

### Fixed
- **Study's Coming up list no longer flashes when a row expands.**
