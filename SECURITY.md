# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability
reporting: on the repository, open the Security tab and choose "Report a vulnerability". Include the
version or commit, what you did, what happened, and what you expected. Please do not include real
personal data in a report.

This is a small project. Reports are read as time allows, and there is no guaranteed response time.

## In scope

- The Swift app, the bundled Python service and the build scripts in this repository.
- Anything that lets text from outside (mail, messages, web pages, school pages, files) cause an
  action the user did not approve, or leak data off the Mac.
- Mistakes in how Harbor stores or reads credentials, Google tokens or cloud keys.
- Ways to bypass an approval, a permission prompt or a stated privacy boundary below.

Out of scope: problems that need an already-compromised Mac or a modified build, issues in macOS,
oMLX or Google's services themselves, and the known limits listed in the README.

## Privacy boundaries Harbor enforces

- **Local by default.** Mail, messages, calendar, reminders and notes are read on the Mac and stored
  in `~/.moe`. Cloud use is opt-in and names what it sends.
- **Approval for risky actions.** Sending mail or messages, calendar changes, AppleScript,
  installing a tool, permanent deletes and shell commands that could send data off the Mac always
  wait for the user. Every action is logged in `~/.moe/audit.jsonl`.
- **Acting unasked is off by default.** The "act for me" mode needs an explicit yes, acts only on
  one-to-one iMessages from saved contacts, never sends, replies, deletes or pays, and shows an Undo.
  An email's From line is never treated as proof of who sent it.
- **Outside text is data.** Content from mail, web pages, school pages, Drive files and attachments
  is never followed as instructions.
- **Keys in the Keychain.** Cloud keys are stored in the macOS Keychain, never in config files or
  logs, and are sent only to the provider's own origin. The Study key is used for Study alone.
- **Google is read-only.** Sign-in asks for read-only scopes. Harbor cannot send, edit or delete
  anything in a Google account, and disconnecting erases what it read.
- **Study never acts on school sites.** It reads pages, and never submits, posts, clicks or types a
  password.
- **Reset.** `/resetharbor` deletes Harbor's stored data and revokes its permissions.

## Supported versions

Only the latest commit on `main` and the most recent release, once there is one.
