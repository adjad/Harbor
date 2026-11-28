"""Skills — new capabilities as installable folders, not code changes.

A skill is a directory under ~/.moe/skills/<name>/ containing a SKILL.md with
YAML frontmatter. Two things it can contribute:

1. **Instructions.** The body of SKILL.md is injected into the system prompt
   when the turn looks relevant (matched against `triggers`). This is how a
   skill teaches Harbor a procedure it doesn't otherwise know — how the user
   likes their standup notes formatted, what their deploy checklist is, which
   accounts matter for expense reports.

2. **Tools.** A skill can declare tools in frontmatter, each backed by a shell
   command template or a script inside the skill folder. They register into the
   normal tool registry and go through the normal policy engine — a skill is a
   packaging mechanism, not a way around the safety layer.

Everything is local files. Installing a skill is copying a folder; there's no
registry to phone home to and nothing about a skill leaves the machine.

Relevance-gating rather than always-injecting matters at this scale: the agent model
runs on a 24GB machine sharing memory with the model weights, and ten always-on
skill bodies would eat the context budget that context.py works to protect. So
the catalog (one line per skill) is always present and the full body arrives
only when triggered, or when the model deliberately calls `use_skill`.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from service.paths import MOE_DIR
from service.skills import trust

SKILLS_DIR = MOE_DIR / "skills"
BUNDLED_SKILLS_DIR = Path(__file__).parent / "bundled"
# A bundled skill's on/off, by name. The signed bundle can't hold it, and a copy
# of the skill in SKILLS_DIR would freeze it at the version it was copied from:
# later releases' edits to its SKILL.md would never reach the user (#414 audit).
_BUNDLED_STATE = ".bundled-enabled.json"  # in SKILLS_DIR
# Held around every read-modify-write of _BUNDLED_STATE, so two toggles at
# once can't each save over the other's change.
_BUNDLED_STATE_LOCK = ".bundled-enabled.lock"
# Where load() moves an older build's toggle copy (dot-named: never loaded).
_RETIRED_DIR = ".retired"

# The body (sha256 of `_parse`'s body) of every bundled SKILL.md Harbor has
# shipped. Before #415, set_enabled copied a bundled skill into SKILLS_DIR to
# flip `enabled`, and that copy outranks the bundled skill for good. One whose
# body (and every other file) is a shipped version is such a copy, not the
# user's own edit, so load() retires it into _BUNDLED_STATE. A copy the user
# changed doesn't match and stays theirs. Add the new hash whenever a bundled
# body changes (tests/test_conversational_skills.py checks the current ones).
_SHIPPED_BODIES: dict[str, frozenset[str]] = {
    "humanizer": frozenset({"d6763c18aeb53e0ff94678b9261c63a84777febb668783807d545c67cfa704a1"}),
    "idea-refine": frozenset({"df143c8dc07f7c2b8cb62c069a9b3af8aa1d2d21871829ec40016c7be9cc89c5"}),
    "interview-me": frozenset({"b73e94b8ac73a65bf61b8068b1a4cbbce13cb062f3879c730346dacc000f580a"}),
    "inbox-cleanup": frozenset({"c4ea8745c3485352fd38fc7536136164655528f881f647f564d4be7fdaf59fd3",
                                "65a3bb6f61f144d9b0ec284dba1bc04afb99e84934f174cf80b446854682a6eb"}),
    "meeting-prep": frozenset({"7397867aba8b9dfa06eb858316113e8f5ff1514af0e18dacf1e0ace34fc81f42"}),
    "week-review": frozenset({"93ce68365dd745a1547bf99429f4b711369d1f9cb0970b3911cf128e944d9ff8"}),
    "file-tidy": frozenset({"e76a9cac83b64a3aa19845e5c7f2d1a3f602accc375776f2959d3c7cfd2c59f2"}),
    "mac-controls": frozenset({"6fe9b434bf7b76c7fec3940ecf93a2b4a43455f38e53d5a2aeeaef1b04b57240",
                               "7968c32f1d5aa6e80101ba8217ebe1a0d0b3c9ea3be7414b20960c148283f188"}),
}

# Frontmatter is fenced by --- on its own line, same convention as every other
# tool that reads SKILL.md-style files. trust.split_frontmatter is the parser
# (a BOM, an empty frontmatter and `---` rule lines in the body all handled);
# this pattern gives the same split for code that wants match groups.
_FRONTMATTER_RE = trust._FRONTMATTER_RE

# Ceiling on one skill body injected into a turn. A skill that wants to teach a
# 20-page procedure has to be split. A longer body loads as broken, with the
# reason in Settings, rather than cut off: a cut can drop the very lines (a
# "never" or "ask first") that the rest depends on. The cap is generous enough
# that no reasonable skill hits it.
MAX_SKILL_MD_BYTES = 1 << 20
MAX_BODY_CHARS = 6000

# Always-on style skills that shape only the messages Harbor writes for the user
# to send (emails, texts, replies, forward notes, scheduled sends, reply
# drafts), never Harbor's own answers. Owner decision 2026-11-10 (a04-settings#1).
COMPOSE_STYLE_SKILLS = frozenset({"humanizer"})

# The tools whose arguments carry a message in the user's voice. A turn whose
# offered tools include one gets compose_style_block() (service/agent/loop.py).
COMPOSE_TOOLS = frozenset({
    "send_message", "send_email", "reply_to_email", "forward_email",
    "schedule_send", "draft_message", "draft_email",
})


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    triggers: list[str] = field(default_factory=list)
    enabled: bool = True
    tools: list[dict] = field(default_factory=list)
    error: str = ""
    # Bypasses `matches()` entirely — injected into every eligible turn
    # instead of only on a trigger hit. See `always_skills_block()`. Such a
    # skill is a reply style for plain chat only (or, for a `compose` skill,
    # for messages written for the user to send): it is never in the agent's
    # catalog, never trigger-loaded there, and use_skill does not hand it out.
    always: bool = False
    bundled: bool = False
    conversation_workflow: bool = False
    # Declared by a fixed workflow skill under metadata.harbor.contract: the
    # built-in tools it may call and which of them change anything. Checked by
    # service.skills.contracts.validate (and tests); never widens a tool's rules.
    contract: dict = field(default_factory=dict)
    # Why a user skill loaded switched off whatever its SKILL.md says: it was
    # added outside Harbor, or changed since the person approved it (skills.trust).
    trust_note: str = ""
    # Loaded by load() from the user's skills folder: such a skill's approval is
    # re-checked before each use (verify_before_run).
    from_user_folder: bool = False

    @property
    def compose(self) -> bool:
        """An always-on style for text Harbor writes FOR the user to send (see
        compose_style_block), never for Harbor's own answers. Keyed by name, not
        frontmatter, so an older or edited copy of the skill behind Settings ›
        Human-sounding replies follows the same rule (owner decision 2026-11-10)."""
        return self.always and self.name in COMPOSE_STYLE_SKILLS

    def matches(self, text: str) -> bool:
        """Whether this skill's triggers fire for `text`.

        Triggers are plain phrases, not regexes — a skill author writing
        "expense report" shouldn't have to think about escaping, and a
        malformed regex in a user-authored file shouldn't be able to break
        routing for every turn. They match on word boundaries: as bare
        substrings, "mute the" fired inside "commute the".
        """
        if not self.enabled:
            return False
        low = text.lower()
        return self.invoked(low) or any(trigger_matches(t, low) for t in self.triggers if t.strip())

    def invoked(self, text: str) -> bool:
        """`text` names this skill outright as @name or $name, as a word of its own:
        not inside an address (user@humanizer.com) or a longer name (@humanizer-v2)."""
        return explicit_mention(self.name, text)

    def as_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "triggers": self.triggers, "enabled": self.enabled,
                "tools": [t.get("name") for t in self.tools],
                "path": str(self.path), "error": self.error,
                "bundled": self.bundled,
                "conversation_workflow": self.conversation_workflow,
                "trust_note": self.trust_note}


def explicit_mention(name: str, text: str) -> bool:
    """`@name` or `$name` in `text` as its own token: the sigil starts a word (nothing
    word-like or another sigil before it) and the name ends one (no letter, digit,
    `_`, `-`, `@` or `.`+word after it)."""
    wanted = str(name or "").lower().strip()
    if not wanted:
        return False
    pattern = (rf"(?<![\w@$.\-])[@$]{re.escape(wanted)}"
               rf"(?![\w\-@]|\.\w)")
    return re.search(pattern, str(text or "").lower()) is not None


def trigger_matches(trigger: str, text: str) -> bool:
    """`trigger` occurs in `text` as whole words (case-insensitive)."""
    phrase = trigger.lower().strip()
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text.lower()) is not None


_skills: dict[str, Skill] = {}


_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0", ""})


def parse_flag(value: object, default: bool, *, unknown: bool | None = None) -> bool:
    """A frontmatter yes/no. YAML reads `enabled: false` as False, but a quoted
    "false" stays a string, and bool("false") is True. `unknown` is the answer for
    anything that is not a recognisable yes or no (the default when not given)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return default if unknown is None else unknown


def _field(meta: dict, harbor_meta: dict, key: str, default: object = None) -> object:
    """A Harbor field: top level wins (older skills), then metadata.harbor. Present
    with any value, an explicit [] or false included, counts as set."""
    if key in meta:
        return meta[key]
    if key in harbor_meta:
        return harbor_meta[key]
    return default


def _read_skill_md(path: Path) -> str:
    """SKILL.md's text, read without following a link, only from a regular file and
    only up to MAX_SKILL_MD_BYTES (a FIFO or /dev/zero planted as SKILL.md must not
    hang or fill the service). Raises OSError (trust.UnsafeFile) otherwise."""
    return trust.read_regular(path, MAX_SKILL_MD_BYTES).decode("utf-8", errors="replace")


def _scope_problem(tools: list[dict]) -> str:
    """Why a declared tool's read_scopes/write_scopes can't be granted, or ""."""
    from service.skills import scopes
    for spec in tools:
        for key in ("read_scopes", "write_scopes"):
            _, why = scopes.parse_scopes(spec.get(key))
            if why:
                return f"tool {spec.get('name') or '?'!s}: {key}: {why}"
    return ""


def _parse(path: Path, *, bundled: bool = False) -> Skill | None:
    """Parse one SKILL.md. A broken skill is reported, never raised — one bad
    file in the folder must not take down skill loading for the rest."""
    try:
        raw = _read_skill_md(path)
    except trust.UnsafeFile as e:
        # Refused whole rather than read in part: a part that is never read
        # could change unseen (candidate review #4 of the 2026-11-25 pentest).
        return Skill(path.parent.name, "", "", path.parent, enabled=False, bundled=bundled,
                     error=f"SKILL.md can't be loaded: {e}")
    except Exception as e:  # noqa: BLE001
        return Skill(path.parent.name, "", "", path.parent, enabled=False,
                     error=f"unreadable: {e}", bundled=bundled)

    split = trust.split_frontmatter(raw)
    if split is None:
        # No frontmatter: still usable as a pure-instructions skill, named after
        # its folder. Lowering the barrier to "drop a markdown file in" is worth
        # more than strictness here.
        body = raw.lstrip("\ufeff").strip()
        skill = Skill(path.parent.name, "", body, path.parent, bundled=bundled)
        if len(body) > MAX_BODY_CHARS:
            skill.error = _too_long(body)
            skill.enabled = False
        return skill

    try:
        meta = yaml.safe_load(split[0]) or {}
        if not isinstance(meta, dict):
            raise ValueError("frontmatter must be a mapping")
    except Exception as e:  # noqa: BLE001
        return Skill(path.parent.name, "", "", path.parent, enabled=False,
                     error=f"bad frontmatter: {e}", bundled=bundled)

    # Harbor's runtime fields live under standard ``metadata`` in new skills so
    # the same package remains valid in other Agent Skills implementations.
    # Top-level keys stay supported for every skill users already installed.
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    harbor_meta = (metadata.get("harbor")
                 if isinstance(metadata.get("harbor"), dict) else {})

    triggers = _field(meta, harbor_meta, "triggers", [])
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",")]
    elif not isinstance(triggers, list):
        triggers = []         # `triggers: 5` or `true` would crash the scan below
    triggers = [t.strip() for t in triggers if isinstance(t, str) and t.strip()]

    tools = _field(meta, harbor_meta, "tools", [])
    if not isinstance(tools, list):
        tools = []
    contract = _field(meta, harbor_meta, "contract", {})

    body = split[1].strip()
    skill = Skill(
        name=str(meta.get("name") or path.parent.name),
        description=str(meta.get("description") or ""),
        body=body,
        path=path.parent,
        triggers=triggers,
        # Anything but a clear yes or no loads switched off.
        enabled=parse_flag(_field(meta, harbor_meta, "enabled"), True, unknown=False),
        tools=[t for t in tools if isinstance(t, dict)],
        always=parse_flag(_field(meta, harbor_meta, "always"), False),
        bundled=bundled,
        conversation_workflow=parse_flag(_field(meta, harbor_meta, "conversation_workflow"), False),
        contract=contract if isinstance(contract, dict) else {},
    )
    if len(body) > MAX_BODY_CHARS:
        skill.error = _too_long(body)
    elif (why := _scope_problem(skill.tools)):
        # skills/tools.py would otherwise drop the bad scope and run the tool
        # with less access than its card promised, or none of what it needs.
        skill.error = why
    if skill.error:
        skill.enabled = False
    return skill


def _too_long(body: str) -> str:
    return (f"its instructions are {len(body):,} characters; the limit is "
            f"{MAX_BODY_CHARS:,}. Split it into smaller skills")


def _contract_problem(skill: Skill) -> str:
    """Why a skill's declared contract doesn't hold (skills.contracts), or ""."""
    if not skill.contract or skill.error:
        return ""
    try:
        import service.tools  # noqa: F401 — every built-in tool, so the contract can be checked
        from service.skills import contracts
        problems = contracts.validate(skill)
    except Exception as exc:  # noqa: BLE001 — an unverifiable contract is not a valid one
        problems = [f"could not be checked ({exc})"]
    return ("its contract does not hold: " + "; ".join(problems)) if problems else ""


def load() -> dict[str, Skill]:
    """(Re)scan bundled skills, then user-installed overrides.

    Bundled workflows ship inside Harbor.app, while ``~/.moe/skills`` remains
    the user's editable layer. A user's copy of a bundled skill (the folder of
    the same name, carrying the same frontmatter name) wins, which lets someone
    customize a bundled workflow without modifying the signed app bundle; it is
    still a user skill, approved like any other (skills.trust). Any OTHER user
    folder that claims a bundled skill's name loads as broken instead: it would
    take that skill's place, and its Settings switch, under a name the person
    knows as Harbor's own.
    """
    global _skills
    found: dict[str, Skill] = {}
    try:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _retire_toggle_copies()
        except Exception:  # noqa: BLE001 — a failed migration must not hide every skill
            pass
        bundled_names: dict[str, str] = {}          # skill name -> its bundled folder name
        for root, bundled in ((BUNDLED_SKILLS_DIR, True), (SKILLS_DIR, False)):
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                md = child / "SKILL.md"
                if not (md.exists() or md.is_symlink()):
                    continue
                try:
                    skill = _parse(md, bundled=bundled)
                except Exception as exc:  # noqa: BLE001 — one bad skill must not hide the rest
                    skill = Skill(child.name, "", "", child, error=f"unreadable skill: {exc}",
                                  enabled=False, bundled=bundled)
                if not skill:
                    continue
                if bundled:
                    bundled_names[skill.name] = child.name
                else:
                    skill.from_user_folder = True
                    owner = bundled_names.get(skill.name)
                    if owner is not None and owner != child.name:
                        skill.error = (f"it is named {skill.name!r}, the name of a skill that "
                                       "ships with Harbor; give it a name of its own")
                        skill.enabled = False
                        # Listed under its folder's name, so the bundled one keeps its place.
                        skill.name = (child.name if child.name not in found
                                      else f"{child.name} (named {skill.name})")
                    # Only a skill the person approved as it is now may be on
                    # (skills.trust); one added or changed behind Harbor's back
                    # loads switched off, with the reason Settings shows. A copy
                    # of a bundled skill still shows in its place (it may be the
                    # person's own edit), so they can switch it on there.
                    note = trust.problem(child)
                    if note:
                        skill.enabled = False
                        skill.trust_note = note
                if (why := _contract_problem(skill)):
                    skill.error = why
                    skill.enabled = False
                found[skill.name] = skill
    except Exception:  # noqa: BLE001 — skills are optional; never break startup
        pass
    for name, enabled in _bundled_state().items():
        skill = found.get(name)
        if skill is not None and skill.bundled and not skill.error:
            skill.enabled = enabled
    _skills = found

    from service.skills.tools import register_skill_tools
    register_skill_tools(found)
    return found


def verify_before_run(skill: "Skill | str | Path") -> str:
    """Why this skill must not run or reach the prompt right now, or "" when it may.

    load() checks a user skill's approval once, but its folder can change while
    Harbor runs (a script swapped, SKILL.md rewritten to widen its scopes). So
    every use re-checks: the skill is loaded, enabled and not broken, and a user
    skill's folder still matches what the person approved (trust.problem). A
    skill that fails is switched off in memory, with the reason Settings shows,
    and its tools are withdrawn; the person switches it back on in Settings.

    Takes a Skill, a skill name or a skill folder. Callers: skills/tools.py's
    runner (before it starts the process) and use_skill (before it returns the
    body); this module's prompt blocks call it for every skill they emit.
    """
    current: Skill | None
    if isinstance(skill, Skill):
        current = _skills.get(skill.name)
        if current is None or current.path != skill.path:
            current = next((s for s in _skills.values() if s.path == skill.path), None)
    elif isinstance(skill, Path):
        try:
            wanted = skill.resolve()
        except (OSError, RuntimeError):
            return "this skill's folder can't be read"
        current = next((s for s in _skills.values() if _same_path(s.path, wanted)), None)
    else:
        current = _skills.get(str(skill))
    if current is None:
        return "this skill is not loaded; reload skills in Settings"
    if current.error:
        return f"the '{current.name}' skill is broken: {current.error}"
    if not current.enabled:
        return current.trust_note or f"the '{current.name}' skill is turned off"
    if not current.from_user_folder:
        return ""                                   # bundled: inside the signed app bundle
    note = trust.problem(current.path)
    if note:
        current.enabled = False
        current.trust_note = note
        try:
            from service.skills.tools import register_skill_tools
            register_skill_tools(_skills)
        except Exception:  # noqa: BLE001 — the skill is off either way
            pass
        return f"the '{current.name}' skill {note}"
    return ""


def _same_path(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == b
    except (OSError, RuntimeError):
        return False


def _usable(skill: Skill) -> bool:
    """Enabled, not broken, and (for a user skill) still as approved."""
    return skill.enabled and not skill.error and verify_before_run(skill) == ""


def _bundled_state() -> dict[str, bool]:
    try:
        raw = json.loads((SKILLS_DIR / _BUNDLED_STATE).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, bool)}


def _save_bundled_state(name: str, enabled: bool, *, unless_set: bool = False) -> bool:
    """Record a bundled skill's on/off: locked, then written to a fresh
    temporary file and renamed over the old one. `unless_set` keeps a choice
    already recorded (a retired copy must not undo a newer toggle)."""
    path = SKILLS_DIR / _BUNDLED_STATE
    try:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SKILLS_DIR / _BUNDLED_STATE_LOCK, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = _bundled_state()
            if unless_set and name in state:
                return True
            state[name] = enabled
            fd, tmp = tempfile.mkstemp(dir=SKILLS_DIR, prefix=_BUNDLED_STATE + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as out:
                    out.write(json.dumps(state, indent=2, sort_keys=True))
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
    except OSError:
        return False
    return True


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _same_files(copy: Path, bundled: Path) -> bool:
    """Every file but SKILL.md is the same in both folders. A link or any other
    special entry in the copy never counts as the same (and is never read)."""
    def files(root: Path) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for p in root.rglob("*"):
            if p.is_symlink() or not (p.is_file() or p.is_dir()):
                raise OSError(f"special entry {p.name}")
            if p.is_file() and p.name != "SKILL.md":
                out[str(p.relative_to(root))] = trust.read_regular(p, MAX_SKILL_MD_BYTES)
        return out
    try:
        return files(copy) == files(bundled)
    except OSError:
        return False


def _toggle_free_meta(md: Path) -> dict | None:
    """A SKILL.md's frontmatter without the `enabled` an older set_enabled
    wrote (top level or under metadata.harbor, adding those maps when absent)."""
    try:
        split = trust.split_frontmatter(_read_skill_md(md))
        meta = yaml.safe_load(split[0]) if split else {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(meta, dict):
        return None if meta else {}
    meta.pop("enabled", None)
    metadata = meta.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("harbor"), dict):
        metadata["harbor"].pop("enabled", None)
        if not metadata["harbor"]:
            del metadata["harbor"]
        if not metadata:
            del meta["metadata"]
    return meta


def _retire_toggle_copies() -> None:
    """Move aside each override an older build's set_enabled made, keeping the
    on/off it carried (see _SHIPPED_BODIES), so the bundled skill applies again."""
    if not BUNDLED_SKILLS_DIR.exists():
        return
    for bundled_dir in sorted(BUNDLED_SKILLS_DIR.iterdir()):
        copy = SKILLS_DIR / bundled_dir.name
        bundled = _parse(bundled_dir / "SKILL.md", bundled=True) if (bundled_dir / "SKILL.md").is_file() else None
        if bundled is None or bundled.error or not (copy / "SKILL.md").is_file():
            continue
        mine = _parse(copy / "SKILL.md")
        if (mine is None or mine.error or mine.name != bundled.name
                or _body_hash(mine.body) not in _SHIPPED_BODIES.get(bundled.name, frozenset())
                or not _same_files(copy, bundled_dir)):
            continue
        frontmatter = _toggle_free_meta(copy / "SKILL.md")
        if frontmatter is None or frontmatter != _toggle_free_meta(bundled_dir / "SKILL.md"):
            continue
        if not _save_bundled_state(bundled.name, mine.enabled, unless_set=True):
            continue
        retired = SKILLS_DIR / _RETIRED_DIR / f"{copy.name}-{int(time.time())}"
        try:
            retired.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(copy), str(retired))
        except OSError:
            continue


def all_skills() -> dict[str, Skill]:
    return dict(_skills)


def get(name: str) -> Skill | None:
    return _skills.get(name)


def set_enabled(name: str, enabled: bool) -> bool:
    """Toggle a skill by rewriting `enabled:` in its frontmatter, so the state
    survives a reload — the folder on disk stays the single source of truth
    rather than a separate index that can drift out of sync with it.

    A bundled skill is the exception: the signed app bundle is not a
    user-state store, and copying the skill out to flip one flag would pin it
    to this release's text. Its on/off goes in SKILLS_DIR/_BUNDLED_STATE instead."""
    skill = _skills.get(name)
    if not skill:
        return False
    if skill.bundled:
        if not _save_bundled_state(skill.name, enabled):
            return False
        load()
        return True
    md = skill.path / "SKILL.md"
    try:
        raw = _read_skill_md(md)
        split = trust.split_frontmatter(raw)
        if split is None:
            # A plain markdown skill: give it a frontmatter that holds the flag.
            meta: dict = {}
            body = raw.lstrip("\ufeff")
        else:
            meta = yaml.safe_load(split[0]) or {}
            body = split[1]
            if not isinstance(meta, dict):
                return False
        metadata = meta.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            meta["metadata"] = metadata
        harbor_meta = metadata.get("harbor")
        if not isinstance(harbor_meta, dict):
            harbor_meta = {}
            metadata["harbor"] = harbor_meta
        # Preserve the legacy location if this is an older user-authored
        # skill; otherwise write the portable extension location.
        if "enabled" in meta:
            meta["enabled"] = enabled
        else:
            harbor_meta["enabled"] = enabled
        _write_atomic(md, "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"
                      + body.lstrip())
        # The person switching a skill on in Settings approves it as it is now.
        if enabled:
            trust.approve(skill.path)
    except Exception:  # noqa: BLE001
        return False
    load()
    return True


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to a fresh file beside `path` and rename it over `path`, so a
    crash or a full disk never leaves half a SKILL.md. Keeps the file's mode."""
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _refuse_links(src: Path) -> str:
    """Why `src` can't be copied in as a skill (a link or a special file inside it), or ""."""
    if src.is_symlink():
        return f"{src} is a link; install the folder it points to instead"
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        for name in [*dirnames, *filenames]:
            entry = Path(dirpath) / name
            try:
                info = os.lstat(entry)
            except OSError as exc:
                return f"can't read {entry}: {exc}"
            if stat.S_ISLNK(info.st_mode):
                return (f"{entry.relative_to(src)} is a link; a skill must hold its own files "
                        "(links are not copied, and could point anywhere)")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                return f"{entry.relative_to(src)} is not a regular file"
    return ""


def _swap_in(staged: Path, dest: Path) -> None:
    """Put the staged folder at `dest`, replacing whatever was there."""
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    os.replace(staged, dest)


def install(source: str) -> dict:
    """Install a skill by copying a folder (or a single .md file) into
    ~/.moe/skills/. Local paths only — deliberately no download-and-run: a
    local-first assistant shouldn't grow a way to execute code fetched from a
    URL, and a skill can register shell-backed tools.

    The installed folder holds exactly what was copied: a skill installed over
    an older one never keeps the older one's scripts (they would be approved
    along with it), and a source holding a link is refused rather than copied."""
    src = Path(source).expanduser()
    if not src.exists():
        return {"ok": False, "error": f"no such path: {src}"}
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        root = SKILLS_DIR.resolve()
        if src.is_file():
            if src.suffix.lower() != ".md":
                return {"ok": False, "error": "a single-file skill must be a .md file"}
            name = src.stem
            if not name or name.startswith("."):
                return {"ok": False, "error": f"{src.name} needs a name that doesn't start with a dot"}
            text = trust.read_regular(src.resolve(), MAX_SKILL_MD_BYTES)
            dest = root / name
            staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
            try:
                (staging / "skill").mkdir()
                (staging / "skill" / "SKILL.md").write_bytes(text)
                _swap_in(staging / "skill", dest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        else:
            if not (src / "SKILL.md").exists():
                return {"ok": False, "error": f"{src} has no SKILL.md"}
            if (why := _refuse_links(src)):
                return {"ok": False, "error": why}
            src = src.resolve()
            dest = root / src.name
            if src == dest:
                # Already the installed copy: re-installing it must not delete it first.
                trust.approve(dest)
                load()
                return {"ok": True, "name": next((s.name for s in _skills.values()
                                                  if s.path.resolve() == dest), dest.name),
                        "path": str(dest)}
            if not src.name or src == root or root in src.parents or src in root.parents:
                return {"ok": False, "error": f"can't install {src} from inside or above the skills folder"}
            # Copy beside the old copy, then swap, so a failed copy leaves the old skill.
            staging = Path(tempfile.mkdtemp(prefix=f".{src.name}-", dir=root))
            try:
                shutil.copytree(src, staging / "skill", symlinks=True)
                # Checked again on the copy: a link swapped in during the copy is
                # copied as a link (symlinks=True), never followed, and caught here.
                if (why := _refuse_links(staging / "skill")):
                    return {"ok": False, "error": why}
                _swap_in(staging / "skill", dest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        # Installed by the person from Settings: approved as copied.
        trust.approve(dest)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    load()
    installed = next((s.name for s in _skills.values() if s.path.resolve() == dest.resolve()), dest.name)
    return {"ok": True, "name": installed, "path": str(dest)}


def uninstall(name: str) -> dict:
    skill = _skills.get(name)
    if not skill:
        return {"ok": False, "error": f"no skill named {name}"}
    if skill.bundled:
        return {"ok": False,
                "error": f"{name} ships with Harbor; disable it instead"}
    try:
        shutil.rmtree(skill.path)
        trust.forget(skill.path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    load()
    reverted = (BUNDLED_SKILLS_DIR / name / "SKILL.md").exists()
    return {"ok": True, "reverted_to_bundled": reverted}


def skills_context_block(user_text: str = "", active_name: str = "",
                         catalog: bool = True) -> str:
    """The per-turn system-prompt contribution: a one-line catalog of every
    enabled skill, plus the FULL body of any whose triggers matched this turn.

    The catalog is what makes an untriggered skill discoverable — without it
    the model has no idea `use_skill` would return anything useful, and a skill
    with imperfect triggers would be permanently invisible. `catalog=False` is
    for a turn that does not offer `use_skill` at all: the model could not act
    on the list, so only matched (or active) skill bodies are returned.
    """
    # An always-on style skill belongs to plain chat (always_skills_block).
    # Each one re-checked against its approval before any of its text is used.
    enabled = [s for s in list(_skills.values()) if not s.always and _usable(s)]
    if not enabled:
        return ""

    matched = [s for s in enabled if s.matches(user_text)]
    active = _skills.get(active_name) if active_name else None
    if active and active in enabled and active not in matched:
        matched.append(active)
    matched_names = {s.name for s in matched}

    out = []
    if catalog:
        out.append("\nInstalled skills — procedures the user has set up for specific "
                   "tasks. Call use_skill(name) to load one's full instructions when a "
                   "request looks like it belongs to that skill:")
        for s in enabled:
            line = f"- {s.name}: {s.description or '(no description)'}"
            if s.name in matched_names:
                line += "  [loaded below]"
            out.append(line)
    elif not matched:
        return ""

    for s in matched:
        out.append(f"\n--- Skill: {s.name} ---\nThe user set up this procedure "
                   f"for requests like this one. Follow it:\n{s.body}")
    return "\n".join(out)


_STOP_WORKFLOW_RE = re.compile(
    r"\b(?:stop|cancel|leave|exit|end)\s+(?:the\s+|this\s+)?"
    r"(?:interview|interviewing|ideation|brainstorm|workflow|skill)\b",
    re.I,
)
# A stop phrase the person negates ("don't stop the interview", "never end this
# workflow") is not a stop: the negation within the few words before it.
_NEGATED_BEFORE_RE = re.compile(
    r"\b(?:don['’]?t|do\s+not|never|not|no\s+need\s+to|won['’]?t|shouldn['’]?t|"
    r"didn['’]?t|doesn['’]?t|without|please\s+don['’]?t)\b(?:\s+\w+){0,2}\s*$",
    re.I,
)


def asks_to_stop_workflow(text: str) -> bool:
    """The person asks to end the active conversational workflow."""
    text = str(text or "")
    for match in _STOP_WORKFLOW_RE.finditer(text):
        if not _NEGATED_BEFORE_RE.search(text[max(0, match.start() - 40):match.start()]):
            return True
    return False


_COMPLETE_RE = re.compile(
    r"<!--\s*harbor-skill-complete:\s*([a-z0-9-]+)\s*-->", re.I)


def select_for_turn(user_text: str, active_name: str = "",
                    last_assistant: str = "") -> str:
    """Choose the one conversational workflow active for this turn.

    Interactive skills are stateful: once the user starts an interview or an
    ideation session, their next answer rarely repeats the original trigger.
    The active name therefore persists in the session store. An explicit stop
    wins immediately, while the completion marker lets a skill release itself
    after delivering its final artifact.
    """
    if asks_to_stop_workflow(user_text):
        return ""
    complete = _COMPLETE_RE.search(last_assistant or "")
    if complete and complete.group(1).lower() == active_name.lower():
        active_name = ""

    enabled = [s for s in list(_skills.values()) if s.conversation_workflow and _usable(s)]
    low = user_text.lower()
    for skill in enabled:
        if skill.invoked(low):
            return skill.name
    for skill in enabled:
        if skill.matches(user_text):
            return skill.name
    if active_name and any(s.name == active_name for s in enabled):
        return active_name
    return ""


def turn_skill_names(user_text: str) -> list[str]:
    """Enabled, error-free skills this user turn invokes: an explicit @name or $name,
    or a trigger phrase. Ordinary and conversational skills alike.

    ``select_for_turn`` only ever returns conversational workflows, so it cannot tell
    the router that an ORDINARY skill is about to be loaded. This can.
    """
    return [s.name for s in list(_skills.values())
            if s.enabled and not s.error and not s.always and s.matches(user_text) and _usable(s)]


# Tools that return skill content or metadata although they are not skill-defined tools.
# harbor_skills lists every installed skill with its enabled state and any load error.
_SKILL_CONTENT_TOOLS = frozenset({"use_skill", "harbor_skills"})


def skill_tool_names() -> frozenset[str]:
    """Tools whose results or schemas carry skill content: ``use_skill``, the skill-listing
    ``harbor_skills`` and every tool an installed skill defines."""
    from service.tools.registry import REGISTRY
    return frozenset(_SKILL_CONTENT_TOOLS | {name for name, tool in REGISTRY.items()
                                             if tool.category == "skill_tool"})


def digest_used_skill(tool_digest: str | None) -> bool:
    """Whether a stored assistant tool digest records a skill being loaded or run,
    so the follow-up to that turn is itself a skill turn."""
    if not tool_digest:
        return False
    used = {part.strip() for part in str(tool_digest).split(",") if part.strip()}
    return bool(used & skill_tool_names())


def selected_skill_block(user_text: str = "", active_name: str = "") -> str:
    """Prompt block for tool-free chat paths.

    Unlike ``skills_context_block`` this omits the full catalog: a plain chat
    turn cannot call ``use_skill``, so advertising every installed procedure
    would spend context without giving the model a way to load them. The
    already-selected workflow body is all that path needs.
    """
    active = _skills.get(active_name) if active_name else None
    selected: list[Skill] = []
    if active and active.conversation_workflow and _usable(active):
        selected = [active]
    else:
        selected = [skill for skill in list(_skills.values())
                    if skill.enabled and not skill.error
                    and skill.conversation_workflow
                    and skill.matches(user_text) and _usable(skill)][:1]
    return "".join(
        f"\n--- Active skill: {skill.name} ---\n"
        "The user chose this conversational workflow. Follow it until it "
        f"finishes or they stop it:\n{skill.body}\n"
        for skill in selected
    )


def always_skills_block() -> str:
    """Body text of every enabled, error-free skill with `always: true` in its
    frontmatter — unconditional, unlike `skills_context_block()`'s
    trigger-matched injection. Deliberately a separate function rather than a
    flag on `skills_context_block()`: the two call sites want different
    things (trigger-matched-only on the tool-calling agent path, always-only
    on the plain-chat path), and a boolean that changes behavior at a
    distance is easy to wire into the wrong place by accident.
    """
    # A compose style (the humanizer) is not a chat style: compose_style_block.
    always_on = [s for s in list(_skills.values())
                 if s.always and not s.compose and _usable(s)]
    if not always_on:
        return ""
    out = []
    for s in always_on:
        out.append(f"\n--- {s.name} ---\n{s.body}")
    return "\n".join(out)


def compose_style_block() -> str:
    """Every enabled compose style (Settings › Human-sounding replies), scoped
    to the text of a message written for the user to send. Empty when off.

    Callers: the reply-draft calls (assistant/email_reply_api.draft_prompt,
    proactive/prepare.draft) append it to their system prompt; the agent loop
    adds it only on a turn that offers a COMPOSE_TOOLS schema, where it shapes
    the body/text argument the model writes. Harbor's own answers never get it.
    """
    styles = [s for s in list(_skills.values()) if s.compose and _usable(s)]
    if not styles:
        return ""
    return "".join(
        f"\n\n--- {s.name}: style for messages you write for the user to send ---\n"
        "Apply these rules only to the words of an email, text, reply or note "
        "the user will send (a send or draft tool's body or text). They do not "
        "apply to your own answer to the user. Never change words the user "
        "dictated or quoted, and every other rule still wins.\n"
        f"{s.body}"
        for s in styles)
