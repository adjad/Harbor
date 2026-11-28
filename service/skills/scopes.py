"""Filesystem scopes a skill tool declares, and how they're shown to the user.

A scope is a directory the tool may read or write, written in the skill's
frontmatter as `~/Downloads` or an absolute path. Scopes exist so a generated
tool can do genuinely useful work on the user's files ("tidy my Downloads",
"rename these photos") while the user still sees, on the confirmation card,
exactly how far it can reach before approving it.

Two rules that are the whole point of the file:

**A tool cannot widen its own scope.** Scopes are read from the skill's
frontmatter and enforced by the kernel (see sandbox.py). Nothing the model
emits at call time can change them — otherwise the declaration would be a
suggestion, and the card would be describing a boundary that doesn't hold.

**Some roots are refused outright, at any breadth.** `/` and `~` as a whole
defeat the point: a card reading "this tool can write anywhere in your home
folder" is not meaningfully different from no containment, and the user cannot
reason about it. System paths are refused because a generated utility has no
legitimate reason to write there and the failure mode is an unbootable machine.
"""
from __future__ import annotations

from pathlib import Path

# Refused as scope roots — too broad to consent to meaningfully, or too
# dangerous to hand a generated script. Checked after resolution, so `~/..`
# and `/Users/you/../..` are caught too.
_REFUSED_ROOTS = {
    Path("/"), Path("/System"), Path("/usr"), Path("/bin"), Path("/sbin"),
    Path("/etc"), Path("/private/etc"), Path("/var"), Path("/private/var"),
    Path("/Library"), Path("/Applications"),
    # Shared temp folders (anyone on the Mac can plant files there) and the
    # mount point of every external and network volume.
    Path("/tmp"), Path("/private/tmp"), Path("/var/tmp"), Path("/private/var/tmp"),
    Path("/Volumes"),
}


def _homes() -> list[Path]:
    """The home folder as written and as resolved: when HOME is a link, a scope
    resolved through it must still be recognised as home (or inside it)."""
    home = Path.home()
    out = [home]
    try:
        out.append(home.resolve())
    except (OSError, RuntimeError):
        pass
    return list(dict.fromkeys(out))


def _credential_places() -> list[Path]:
    """The key and credential folders the sandbox never lets a skill read
    (sandbox._NEVER_READ), under each spelling of home, and resolved."""
    from service.skills.sandbox import _NEVER_READ
    out: list[Path] = []
    for raw in _NEVER_READ:
        for home in _homes():
            place = Path(raw.replace("~", str(home), 1)) if raw.startswith("~") else Path(raw)
            out.append(place)
            try:
                out.append(place.resolve())
            except (OSError, RuntimeError):
                pass
    return list(dict.fromkeys(out))


def _protected_places() -> list[Path]:
    from service.paths import MOE_DIR
    from service.safety.policy import _code_exec_roots
    places = [MOE_DIR, Path.home() / ".moe", *_code_exec_roots()]
    out: list[Path] = []
    for place in places:
        out.append(Path(place))
        try:
            out.append(Path(place).resolve())
        except (OSError, RuntimeError):
            pass
    return list(dict.fromkeys(out))


def _home_or_above(path: Path) -> bool:
    return any(path == home or path in home.parents for home in _homes())


def _overlap(path: Path, places: list[Path]) -> str:
    for place in places:
        if path == place or path.is_relative_to(place):
            return str(place)
        # Containing one counts, but home and the folders above it hold them all
        # and are refused (or allowed) by the rules below.
        if place.is_relative_to(path) and not _home_or_above(path):
            return str(place)
    return ""


def _code_run_overlap(path: Path) -> str:
    return _overlap(path, _protected_places())


def _refused(path: Path) -> str:
    homes = _homes()
    if path in _REFUSED_ROOTS:
        return f"{path} is too broad or too sensitive to grant a generated tool"
    if path in homes:
        return ("your whole home folder is too broad to grant — name the "
                "specific folder the tool needs (e.g. ~/Downloads)")
    if any(path == home.parent for home in homes):      # /Users
        return "every user's home folder is too broad to grant"
    # Keys and credentials: the sandbox never lets a tool read them, so a scope
    # naming one (or holding one) would tell the person something untrue.
    hit = _overlap(path, _credential_places())
    if hit:
        return f"{path} reaches {hit}, which holds keys or credentials a generated tool never reads"
    # Harbor's own folder and the places whose files run as code later (login
    # items, shell startup files, git config, the app bundle): a scope that is
    # one, sits inside one, or contains one is refused. A skill granted
    # ~/Library could otherwise plant a LaunchAgent, and one granted ~/.moe
    # could rewrite grants or other skills (candidate review #1).
    hit = _code_run_overlap(path)
    if hit:
        return f"{path} reaches {hit}, where files run as code later or Harbor keeps its own data"
    # A scope INSIDE a system root is fine (/usr/local/share/x); a scope that
    # IS one, or contains one, is not.
    for root in _REFUSED_ROOTS:
        if root != Path("/") and root.is_relative_to(path):
            return f"{path} contains {root}, which a generated tool must not reach"
    return ""


def parse_scopes(raw: object) -> tuple[list[Path], str]:
    """Frontmatter value -> (resolved paths, error). Accepts a string or list."""
    if raw in (None, "", []):
        return [], ""
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, list):
        return [], f"expected a path or list of paths, got {type(raw).__name__}"

    out: list[Path] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            path = Path(text).expanduser().resolve()
        except Exception as e:  # noqa: BLE001
            return [], f"bad path {text!r}: {e}"
        if (why := _refused(path)):
            return [], why
        out.append(path)
    return out, ""


def describe(read_scopes: list[Path], write_scopes: list[Path],
             sandboxed: bool = True) -> str:
    """One line for the confirmation card: what this tool can reach.

    Says "cannot read or write any of your files" for the common no-scope case
    rather than staying silent — the absence of a warning is easy to read as
    "nobody checked", and the containment is the reassuring part.
    """
    if not sandboxed:
        return ("⚠ NOT sandboxed on this system — this tool runs with your full "
                "file access. Read the code carefully.")

    def fmt(paths: list[Path]) -> str:
        def one(p: Path) -> str:
            for home in _homes():
                if p.is_relative_to(home):
                    return f"~/{p.relative_to(home)}"
            return str(p)
        return ", ".join(one(p) for p in paths)

    if not read_scopes and not write_scopes:
        return "Sandboxed: cannot read or write any of your files."
    # A path granted for writing is implicitly readable, so listing it under
    # both reads as two separate grants for what is one folder.
    read_only = [p for p in read_scopes if p not in write_scopes]
    parts = []
    if write_scopes:
        parts.append(f"read and modify {fmt(write_scopes)}")
    if read_only:
        parts.append(f"read {fmt(read_only)}")
    return "Sandboxed. This tool can " + "; ".join(parts) + " — nothing else."
