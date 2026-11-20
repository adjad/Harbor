# Changelog

All notable changes to Harbor are documented here.

## [Unreleased]

## [0.1.8] - 2026-11-20

See [docs/releases/0.1.8.md](docs/releases/0.1.8.md) for the release notes.

### Changed
- **Setup** asks only for Calendar, Reminders, Mail & Notes, and Full Disk Access. Harbor asks for each of the others when a task first needs it: Messages at the first approved text, Contacts from a row in Brief once texts show numbers, and Notifications at the first alert. The microphone, Screen Recording and Accessibility were already asked at first use. Settings › Privacy still lists every permission.
- **Day**'s row of days starts on today: today is the first card, followed by the next six days.

## [0.1.7] - 2026-11-18

See [docs/releases/0.1.7.md](docs/releases/0.1.7.md) for the release notes.

### Changed
- **Tab switches** from a short tab to a tall or wide one are one glide: the window grows before the panel. The two-column button slides the panel, and with Reduce Motion on it cuts straight to the final size.
- **Setup** shows a permission as granted right away; a slower, older check can no longer overwrite a newer answer. Screen Recording and Full Disk Access can still need Harbor to reopen.
- **Day** switches days in one fade: the row of days sits at the top and stays put, the day you leave stays faded until the next is ready, and the panel resizes once.
- **The notch** fits the camera housing on every notched Mac: a body just inside it, with top corners that curve out into the menu bar.
- **Alarms** accept "7 a.m.", "at 6pm", "noon" and "midnight".

### Fixed
- Cancelling in Ask removes the exact event or reminder from Calendar or Reminders (one occurrence of a repeating event), says "Cancelled" only after reading back that it is gone, and the approval card names the item, its date and time, and its calendar or list. Marking a reminder done completes it. Harbor says plainly when it cannot: someone else's invitation, read-only or subscribed calendars, a whole repeating series. Day shows Cancel next to a reminder's Plan time; it asks once in the row, then deletes that exact reminder in Reminders.
- A text Messages gives no delivery evidence for is reported as handed to Messages, not "Sent"; no second copy after Messages reports an error having already queued it; a blocked or declined send gives a short reason.
- A setting, clipboard or screenshot action that was refused is no longer reported as done.

### Security
- Only the Harbor app can talk to Harbor's background service (a new key each launch). No compiled-code cache is kept in a user-writable folder (start is about a second slower). "Run in Terminal" asks on a card inside the panel that shows the whole script; nothing runs until the second press. The tool-server settings file is refused if it is a link or writable by others.
- For developers: the phone gateway and scripts that call the running app's service are refused by the new key.

## [0.1.6] - 2026-11-15

See [docs/releases/0.1.6.md](docs/releases/0.1.6.md) for the release notes.

### Changed
- **Brief.** The first zone (the left half when wide) is "Ready for you": the drafted reply, a row for each thing Harbor prepared or found, and what it added for you, with Undo. The second zone is your lists.
- **Assignments due today or tomorrow** lead the Brief, each with its study guide and a link to its page.
- **Day.** A seven-day row, a "Next up" card and "Later today", from a new read-only week summary.

### Fixed
- Texts go out on the line the conversation already uses; a failed text no longer blocks the chat; an email to an address you typed reaches the approval card; reminders with a time and a title are created as asked.
- Keys saved before the app was renamed are copied on first use, behind macOS's own prompt, and Ask says "Reconnect your cloud AI key" when one is missing.
- The Day says when it cannot read your calendar instead of saying nothing is planned.

## [0.1.5] - 2026-11-14

See [docs/releases/0.1.5.md](docs/releases/0.1.5.md) for the release notes.

### Added
- **Two-zone Brief.** What Harbor has ready and what is on your plate, with "Found in your mail" in the right half of the two-column Brief.
- **Two-column Day.** One two-column setting for Brief and Day; Old reminders closes the left half, Still to fit and School this week fill the right.
- **Find closes the tabs it opened,** behind macOS's own permission to control the browser; a refusal, failure or timeout leaves them open and says so.
- **Class notes in Ask** also search the slides added in Study.

### Changed
- **Tab switches to and from the wide Brief and wide Day** are one motion.
- **Find** checks every redirect against the never-open rules, keeps course and assignment addresses on the page's own site, refuses form, poll, payment and meeting sites, and stops at once when asked.

### Fixed
- Unnamed group rows ("in a group of 3"), the Brief's calendar line, repeated to-do quotes, course codes in to-do titles, the Day summary count, a cut-off Still-to-fit reason, and study-block Undo and deletion seen from Calendar.

## [0.1.0] - 2026-11-14

First public release. See [docs/releases/0.1.0.md](docs/releases/0.1.0.md) for the release notes.

### Added
- **Ask.** Chat with tools over mail, messages, calendar, reminders, notes and the web; drafts, events, reminders and opening apps, with risky actions waiting for approval; voice; Smart Search (Command-Shift-F); answers about course slides cite the deck and page.
- **Brief.** A summary of what needs you, refreshed a few times a day, as cards you can act on or dismiss; a school row; "Ready for tomorrow" for a quiz or exam with matching slides; weak spots; a school item never shows twice across Brief and Day; an optional "Let Harbor act for me" switch, off until you say yes, with an Undo on each action.
- **Day.** A time-ordered agenda with school deadlines, suggested study blocks, the slides that cover each deadline and a "Review (15 min)" item; "Add to calendar" books a study block in your calendar, and Undo removes it again.
- **Study.** Courses and assignments read from open Canvas pages, read-only, with duplicate course rows merged; Google Drive Docs, Slides and PDFs as sources; study guides, practice quizzes, short lessons and flash cards written by a cloud model with a key used for Study only; slides and PDFs read page by page on the Gemini API, with reading and failed states and Try again.
- **Find course materials.** Harbor opens only a course's own Pages, Discussions and section list pages in your signed-in browser, read-only, up to 100 pages a run with a pause between opens. It never opens tests, quizzes, surveys, graded or proctored work, submission or attempt pages, payments, meetings or outside tools, and lists each refused item with the reason. The Study model decides link by link which outside links and public PDFs are safe to read, fetched without cookies or sign-in; nothing is added until you tick it. Lecture recordings are listed as links and never opened, and the panel stays open while a walk runs.
- **Gemini and Google.** The Gemini API as a cloud provider; a "Gemini handles everything" switch in Settings > Advanced that also sends pictures and voice to Gemini; response schemas for Study material; whole-course guides with long context; read-only Gmail, Calendar and Drive through Google sign-in.

### Fixed
- **Study's Coming up list no longer flashes when a row expands.**
