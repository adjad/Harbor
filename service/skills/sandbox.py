"""Kernel-enforced filesystem containment for generated / installed skill tools.

Skill tools were only ever contained on paper. `_make_runner` checks that the
SCRIPT FILE lives inside its own skill folder — but once that script starts it
is an ordinary child process of a non-App-Sandboxed app, so it inherits the
user's full filesystem rights. A generated tool asked to "tidy my Downloads"
could just as easily read ~/.ssh or empty ~/Documents, and the only thing
standing between the user and that was reading the code on the confirmation
card. That is a review step, not a boundary.

macOS's `sandbox-exec` gives a real one: the kernel refuses the syscall, so it
holds regardless of what the generated code actually does — including `../..`
traversal, `os.remove`, and subprocesses it spawns itself.

The profile is deny-by-default for WRITES with an explicit allowlist, and
denies READS across the user's home except for the scopes the tool declared.
Everything else (interpreter startup, dyld cache, /usr, /System) stays allowed,
because locking those down stops Python from booting at all — verified: a
`(deny default)` profile kills the interpreter with SIGABRT before it runs a
line, which reads as "the tool is broken" rather than "the tool was contained".

`sandbox-exec` is formally deprecated by Apple but is still present and
functional on macOS 27, and is the same mechanism Chrome and others rely on.
If a future OS removes it, `available()` returns False and skill tools fall
back to running unsandboxed — the confirmation card says so explicitly rather
than implying a containment that isn't there.
"""
from __future__ import annotations

import shutil
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Always writable: these device nodes, for ordinary stdio. Scratch space is the
# tool's own folder under SCRATCH_ROOT (TMPDIR points there), not the skill's
# folder (which holds the SKILL.md and script the person approved) and not the
# shared /private/var/folders.
_ALWAYS_WRITE = [
    '(literal "/dev/null")', '(literal "/dev/stdout")', '(literal "/dev/stderr")',
    '(literal "/dev/dtracehelper")',
]

# No handing work to a process outside the box (pentest #3): Launch Services
# (`open`, which can start an app or a .command file), Apple Events (osascript),
# the pasteboard, privacy grants and the secrets broker, and no new launchd jobs.
# The same denies the Ideate profile uses (service/ideate/sandbox.py).
_MACH_DENY = [
    '(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd")'
    ' (global-name "com.apple.coreservices.appleevents") (global-name "com.apple.pasteboard.1")'
    ' (global-name-prefix "com.apple.lsd."))',
    '(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.nsurlsessiond")'
    ' (global-name "com.apple.tccd") (global-name-prefix "com.apple.securityd")'
    ' (global-name-prefix "com.apple.windowserver") (global-name-prefix "com.apple.screencapture"))',
    "(deny job-creation)",
    # An allow-default profile must also refuse these outright: sending an Apple
    # Event and asking Launch Services to open something do not need the
    # mach-lookups above once a port is known (checked: osascript still reached
    # System Events without them).
    "(deny appleevent-send)",
    "(deny lsopen)",
]


def scratch_dir(skill_dir: Path) -> Path:
    """The one folder a skill tool may write without a declared scope."""
    from service.paths import MOE_DIR
    return MOE_DIR / "skill-scratch" / Path(skill_dir).name

# Where personal data actually lives. Reads are denied across these roots and
# re-allowed only for declared scopes.
#
# This is a deny-list rather than the stricter deny-everything-then-allow, for
# an empirical reason: a global `(deny file-read*)` — even `file-read-data`
# alone, with /usr, /System, /Library, /opt and the dyld cache explicitly
# allowed back — kills the interpreter with SIGABRT before it executes a line.
# The user sees "the tool is broken", not "the tool was contained". These four
# roots cover every location user data realistically sits in (all home folders,
# mounted external and network volumes, the shared temp dirs, root's home);
# what stays readable is OS and application code, which holds nothing personal.
_READ_DENY_ROOTS = [
    "/Users", "/Volumes", "/private/tmp", "/private/var/root",
    # Every app's per-user temp and cache folders; a tool's own temp is its scratch.
    "/private/var/folders",
]

# Read-denied even when a broader scope is granted. A tool given ~/Documents
# has no business in the user's keys or Harbor's own credential/session stores,
# and these are the paths where a mistake is unrecoverable rather than annoying.
_NEVER_READ = [
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.netrc", "~/.config/gh", "~/.config/gcloud",
    "~/Library/Keychains",
    "~/.moe/sessions.db", "~/.moe/facts.db", "~/.moe/assistant.db",
    "~/.moe/gmail.json", "~/.moe/mcp.json", "~/.moe/google_workspace", "~/.moe/grants.json",
    "~/.moe/access_mode.json", "~/.moe/phone_gateway",
]
# The same Harbor files under HARBOR_HOME, when it points somewhere else.
_NEVER_READ_HARBOR = ["sessions.db", "facts.db", "assistant.db", "gmail.json", "mcp.json",
                    "google_workspace", "grants.json", "access_mode.json",
                    # the phone gateway's link key and paired-device secrets (pentest #20)
                    "phone_gateway"]

# Network: a skill tool may reach the internet (a weather skill must), but not
# this Mac's own services. Harbor's API on 127.0.0.1:8765 treats some requests
# as the user's own tap, and oMLX takes load/unload commands; neither is a
# skill's business. Verified: with this rule a loopback connect fails with
# EPERM while an outside host still connects.
_NETWORK_DENY = ['(deny network-outbound (remote ip "localhost:*"))']


def available() -> bool:
    return Path(SANDBOX_EXEC).exists() and shutil.which("sandbox-exec") is not None


def _q(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def build_profile(skill_dir: Path, read_scopes: list[Path],
                  write_scopes: list[Path]) -> str:
    """A sandbox profile allowing reads/writes only within the given scopes.

    The skill's own folder is readable but never writable: it holds the
    SKILL.md (with the tool's scopes) and the script the person approved, so a
    tool may not widen its own scopes or rewrite itself (pentest #2). Its
    writable scratch is scratch_dir().
    """
    home = Path.home()
    scratch = scratch_dir(skill_dir)
    # Both spellings: HARBOR_HOME may sit behind a link (/tmp -> /private/tmp).
    scratches = list(dict.fromkeys([scratch, scratch.resolve()]))
    # write scopes imply read — a tool that can modify a folder can obviously
    # see it, and declaring both separately for the same path is pure friction.
    readable = list(dict.fromkeys([*read_scopes, *write_scopes]))
    lines = [
        "(version 1)",
        "(allow default)",
        # --- writes: deny everything, then re-allow the declared scopes ---
        "(deny file-write*)",
        *(f"(allow file-write* {frag})" for frag in _ALWAYS_WRITE),
        *(f'(allow file-write* (subpath "{_q(p)}"))' for p in scratches),
        *(f'(allow file-write* (subpath "{_q(p)}"))' for p in write_scopes),
        # --- reads: deny the user-data roots, re-allow declared scopes ---
        *(f'(deny file-read* (subpath "{_q(p)}"))' for p in _spellings(_READ_DENY_ROOTS)),
        f'(allow file-read* (subpath "{_q(skill_dir.resolve())}"))',
        *(f'(allow file-read* (subpath "{_q(p)}"))' for p in scratches),
        *(f'(allow file-read* (subpath "{_q(p)}"))' for p in readable),
        *_MACH_DENY,
    ]
    # Re-deny the sensitive paths LAST so they win even if a granted scope
    # (e.g. all of ~) would otherwise cover them. In SBPL the last matching
    # rule applies, so ordering here is load-bearing, not stylistic.
    from service.paths import MOE_DIR
    never = [Path(raw.replace("~", str(home), 1)) for raw in _NEVER_READ]
    never += [MOE_DIR / name for name in _NEVER_READ_HARBOR]
    for p in dict.fromkeys(_spellings(never)):
        lines.append(f'(deny file-read* (subpath "{_q(p)}"))')
        lines.append(f'(deny file-write* (subpath "{_q(p)}"))')
    # Never writable, whatever scope was granted (a scope of ~ covers them): every
    # skill's folder, this skill's included, and the record of what was approved.
    from service.skills import SKILLS_DIR
    from service.skills.trust import TRUST_PATH
    # Nor Harbor's folder (all of it but this tool's scratch) or a place whose files
    # run as code later, whatever scope reached it (candidate review #1).
    from service.safety.policy import _code_exec_roots
    protected = [MOE_DIR, home / ".moe", *_code_exec_roots()]
    for p in dict.fromkeys(_spellings(protected)):
        lines.append(f'(deny file-write* (subpath "{_q(p)}"))')
    lines += [f'(allow file-write* (subpath "{_q(p)}"))' for p in scratches]
    for p in dict.fromkeys(_spellings([skill_dir, SKILLS_DIR, TRUST_PATH])):
        lines.append(f'(deny file-write* (subpath "{_q(Path(p))}"))')
    lines.extend(_NETWORK_DENY)
    # Harbor's and the engines' ports on every address, not only loopback: either
    # may listen on this Mac's LAN address too (candidate review #4).
    from service.safety import shell_sandbox
    try:
        ports = dict.fromkeys([*shell_sandbox.harbor_ports(), *shell_sandbox.engine_ports()])
    except Exception:  # noqa: BLE001 - the loopback deny above still applies
        ports = {}
    lines += [f'(deny network-outbound (remote ip "*:{port}"))' for port in ports]
    return "\n".join(lines) + "\n"


def _spellings(paths) -> list[Path]:
    """Each path as written and resolved: a deny for one spelling of a linked path
    does not cover the other."""
    out: list[Path] = []
    for p in paths:
        out.append(Path(p))
        try:
            out.append(Path(p).resolve())
        except (OSError, RuntimeError):
            pass
    return list(dict.fromkeys(out))


def wrap_argv(argv: list[str], profile_path: Path) -> list[str]:
    return [SANDBOX_EXEC, "-f", str(profile_path), *argv]
