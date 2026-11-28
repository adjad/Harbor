"""Tools contributed by installed skills, plus the `use_skill` loader.

Two hard rules shape this file:

**A skill-declared tool always runs through the policy engine.** Its category
is forced to `skill_tool` (confirm-tier, denied in view-only) regardless of
what the skill's frontmatter says. Letting a skill self-declare `fs_read` would
let any dropped-in folder mark a shell command as auto-allowed — a skill is a
packaging format, not a trust boundary. The narrow escape hatch is a standing
grant (service/safety/grants.py), which is the user's decision, not the
skill's.

**Arguments are never interpolated into a shell string.** The command template
is shlex-split FIRST, then placeholders are substituted per-token, and the
result is exec'd with shell=False. So an argument containing `; rm -rf ~` stays
one argv element that the program receives as a literal string, instead of
becoming a second command. Building the string first and splitting after would
be a textbook injection hole, and the model — which is what fills these
arguments in — is exactly the untrusted input path that makes it exploitable.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from service.identity import child_environment
from service.skills import sandbox, scopes
from service.tools.registry import REGISTRY, register

# Tool names this module has registered, so a reload can retract tools whose
# skill was uninstalled or disabled instead of leaving them callable forever.
_registered: set[str] = set()
# Marks a runner this module built; router.model_led trusts only those for a
# completion receipt (authentic_completion_tool).
_COMPLETION_TOKEN = object()


def authentic_completion_tool(tool) -> bool:
    """A registered skill tool whose function is this module's own runner."""
    return (getattr(tool, "category", "") == "skill_tool" and tool.name in _registered
            and getattr(tool.func, "_harbor_completion", None) is _COMPLETION_TOKEN
            and getattr(tool.func, "_harbor_tool_name", None) == tool.name)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_TIMEOUT_S = 120
_MAX_OUTPUT = 8000
# Bytes read from a tool before it is stopped: what _MAX_OUTPUT characters can
# take in UTF-8, so the service never holds more than this of one tool's output
# (pentest #8: a printing loop used to grow the service for the whole timeout).
_MAX_OUTPUT_BYTES = _MAX_OUTPUT * 4
_READ_CHUNK = 16384
_STOP_DRAIN_S = 3.0


async def _read_capped(proc) -> tuple[bytes, bool]:
    """The tool's output, at most _MAX_OUTPUT_BYTES of it, and whether it was cut
    off. A tool that prints more is stopped as soon as it passes the ceiling."""
    kept = bytearray()
    while True:
        chunk = await proc.stdout.read(_READ_CHUNK)
        if not chunk:
            break
        room = _MAX_OUTPUT_BYTES - len(kept)
        kept += chunk[:max(room, 0)]
        if len(chunk) > room:
            await _stop(proc)
            return bytes(kept), True
    await proc.wait()
    return bytes(kept), False
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
TRUE_MARK = "\0true"
_GONE = object()


def _omitted(value) -> bool:
    """An argument the caller left out. 0 is a value (volume 0), not an omission."""
    return value is None or value is False or (isinstance(value, str) and value == "")


def _text(value) -> str:
    return "" if value is None else str(value)


def _build_argv(template: str, args: dict) -> list[str] | str:
    """Split the template, then substitute. Returns argv, or an error string.

    A token that is exactly one placeholder becomes exactly one argv element
    holding the raw value. A token with a placeholder embedded in other text
    (`--out={path}`) is substituted inside that single token. Either way the
    value can never introduce a new word, an operator, or a second command.
    """
    try:
        tokens = shlex.split(template)
    except ValueError as e:
        return f"(bad command template in this skill: {e})"

    # None marks a placeholder-only token whose argument was omitted.
    argv: list[str | None] = []
    for token in tokens:
        m = _PLACEHOLDER_RE.fullmatch(token)
        if m:
            value = args.get(m.group(1))
            if _omitted(value):
                argv.append(None)          # optional argument the caller omitted
            elif value is True:
                argv.append(TRUE_MARK)     # resolved against a preceding flag below
            else:
                text = str(value)
                if text.startswith("-") and not _NUMBER_RE.fullmatch(text):
                    return (f"(the '{m.group(1)}' argument may not start with '-': it "
                            "could be read as an option by the program)")
                argv.append(text)
            continue
        names = _PLACEHOLDER_RE.findall(token)
        if names and any(_omitted(args.get(n)) for n in names):
            # "--app={app}" with no app must vanish, never become "--app=None".
            argv.append(_GONE)
            continue
        argv.append(_PLACEHOLDER_RE.sub(
            lambda mm: _text(args.get(mm.group(1))), token))

    # A flag whose value was omitted goes with it; a boolean flag is bare.
    out: list[str | None] = []
    for i, item in enumerate(argv):
        prev_is_flag = (i > 0 and tokens[i - 1].startswith("-")
                        and not _PLACEHOLDER_RE.search(tokens[i - 1]))
        if item is TRUE_MARK:
            if prev_is_flag:
                continue                   # the flag alone says "on"
            item = "true"
        if item is None and prev_is_flag and out and out[-1] == tokens[i - 1]:
            out.pop()                      # drop the now-pointless flag
            continue
        out.append(item)
    out = [x for x in out if x is not _GONE]
    # Trailing omissions simply end the argument list. One in the middle keeps
    # its place as an empty argument, so later positional arguments do not shift
    # into it.
    while out and out[-1] is None:
        out.pop()
    return ["" if x is None else x for x in out]


def _make_runner(skill_dir: Path, spec: dict, name: str = "", *, bundled: bool = False):
    template = str(spec.get("command") or "")
    script = str(spec.get("script") or "")
    # Apple Events only for a skill that ships inside Harbor's signed bundle, and
    # only to the closed set of apps in sandbox.APPLE_EVENT_TARGETS. A skill the
    # user installed or Harbor generated cannot declare them: the key is ignored.
    declared = spec.get("apple_events") if bundled else None
    apple_events = sandbox.apple_event_destinations(
        declared if isinstance(declared, list) else [])
    # A bad scope is reported when the skill's tools are registered
    # (register_skill_tools sets the skill's error); here it can only be empty.
    read_scopes, _ = scopes.parse_scopes(spec.get("read_scopes"))
    write_scopes, _ = scopes.parse_scopes(spec.get("write_scopes"))

    async def run(**args) -> str:
        if script:
            path = (skill_dir / script).resolve()
            # Containment check: a skill's own folder is the only place it may
            # execute from, so `script: ../../../usr/bin/something` can't turn a
            # skill install into arbitrary-binary execution.
            try:
                path.relative_to(skill_dir.resolve())
            except ValueError:
                return f"(this skill's script path escapes its own folder: {script})"
            if not path.exists():
                return f"(this skill's script is missing: {script})"
            # Absolute interpreters: PATH is the model-visible environment's, and
            # python3 from it is not the Python Harbor itself runs on.
            interpreter = {".py": sys.executable, ".sh": "/bin/bash",
                           ".js": shutil.which("node") or "node"}.get(path.suffix.lower())
            argv = ([interpreter, str(path)] if interpreter else [str(path)])
            for key, value in args.items():
                if _omitted(value):
                    continue
                if value is True:
                    argv.append(f"--{key}")            # a bare flag
                elif str(value).startswith("-"):
                    argv.append(f"--{key}={value}")    # never read as an option
                else:
                    argv += [f"--{key}", str(value)]
        else:
            built = _build_argv(template, args)
            if isinstance(built, str):
                return built
            argv = built
        if not argv:
            return "(this skill's tool has no command to run)"

        # Kernel-enforced containment. The script-path check above only proves
        # the FILE sits in the skill's folder; it says nothing about where the
        # running process reaches. Without this, a generated tool inherits the
        # user's full filesystem rights the moment it starts.
        profile_file = None
        # The tool's own scratch folder, outside its skill folder (sandbox.scratch_dir).
        scratch = sandbox.scratch_dir(skill_dir)
        try:
            scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            return f"(couldn't prepare this skill's scratch folder: {e})"
        if not sandbox.available() and (why := sandbox.refusal()):
            return f"(this skill's tool was not run: {why})"
        if sandbox.available():
            profile = sandbox.build_profile(skill_dir, read_scopes, write_scopes, apple_events)
            fd, profile_path = tempfile.mkstemp(suffix=".sb", prefix="harbor-skill-")
            with os.fdopen(fd, "w") as f:
                f.write(profile)
            profile_file = Path(profile_path)
            argv = sandbox.wrap_argv(argv, profile_file)

        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(skill_dir),
                    env={**child_environment(), "HARBOR_SKILL_DIR": str(skill_dir),
                         "HARBOR_SKILL_SCRATCH": str(scratch), "TMPDIR": str(scratch) + "/",
                         # No .pyc beside the skill: its folder is fingerprinted (skills/trust.py).
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    # Its own process group, so a stop reaches the children a script starts.
                    start_new_session=True,
                )
            except FileNotFoundError:
                return f"(couldn't run this skill's tool — {argv[0]} isn't installed)"
            except Exception as e:  # noqa: BLE001
                return f"(couldn't run this skill's tool: {e})"

            try:
                out, cut = await asyncio.wait_for(_read_capped(proc), _TIMEOUT_S)
            except asyncio.TimeoutError:
                await _stop(proc)
                return f"(this skill's tool timed out after {_TIMEOUT_S}s)"
            except BaseException:
                # The turn was cancelled (Ask closed, Stop): the tool must not run on.
                await _stop(proc)
                raise
        finally:
            # The profile must outlive the process, so it's removed only once
            # the run is fully done — including the early-return paths above.
            if profile_file:
                profile_file.unlink(missing_ok=True)

        text = (out or b"").decode(errors="replace").strip()
        # Judged on everything captured, before the text is shortened: the
        # sandbox's "Operation not permitted" can sit past the first 8000
        # characters of a chatty tool and must still be named.
        denied = (proc.returncode != 0
                  and ("Operation not permitted" in text or "Errno 1" in text))
        if denied:
            allowed = scopes.describe(read_scopes, write_scopes)
            return (f"(blocked by the sandbox — this tool tried to reach a "
                    f"file outside what it's allowed. {allowed})\n{text[-_MAX_OUTPUT:]}")
        if cut:
            return (text[:_MAX_OUTPUT] + f"\n…[truncated: this skill's tool printed more than "
                    f"{_MAX_OUTPUT} characters and was stopped]")
        if len(text) > _MAX_OUTPUT:
            text = text[:_MAX_OUTPUT] + "\n…[truncated]"
        if proc.returncode != 0:
            return f"(exit code {proc.returncode})\n{text}"
        # The host-observed exit status 0 is the completion boundary, not the
        # skill's own words (router.model_led.EXTENSION_COMPLETION_MODULES).
        from service.router.model_led import BuiltinCompletion
        return BuiltinCompletion(text or "(done — no output)", tool_name=name, completion_code=0)

    run._harbor_completion = _COMPLETION_TOKEN
    run._harbor_tool_name = name
    return run


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """Kill a skill tool's whole process group and reap it."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    # asyncio's wait() also waits for the pipes to close, and a full stdout
    # pipe whose reader is paused never does: drain it (discarding) first, for
    # at most _STOP_DRAIN_S in all. A grandchild that left the process group
    # (setsid) keeps the pipe open and could print forever, so the pipe is then
    # closed on our side and the wait given up.
    if proc.stdout is not None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(_STOP_DRAIN_S):
                while await proc.stdout.read(65536):
                    pass
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(proc.wait()), _STOP_DRAIN_S)


def _tool_problems(skill) -> str:
    """What is wrong with the tools a skill declares, or ""."""
    problems = []
    for spec in skill.tools:
        if not isinstance(spec, dict):
            problems.append("a tool entry is not a mapping")
            continue
        raw = str(spec.get("name") or "").strip()
        if not _NAME_RE.match(raw.lower()):
            problems.append(f"tool name {raw!r} is invalid (lowercase letters, digits and "
                            "underscores, starting with a letter, 2-49 characters)")
        for key in ("read_scopes", "write_scopes"):
            _, why = scopes.parse_scopes(spec.get(key))
            if why:
                problems.append(f"tool {raw or '?'}: {key}: {why}")
    return "; ".join(problems)


def register_skill_tools(skills: dict) -> list[str]:
    """Register every enabled skill's declared tools; retract stale ones."""
    for name in _registered:
        REGISTRY.pop(name, None)
    _registered.clear()

    added: list[str] = []
    # Tool names already claimed in this pass, by skill: a later skill may not
    # take one over (standing grants are keyed by tool name; pentest #23).
    claimed: dict[str, str] = {}
    for skill in skills.values():
        if not skill.enabled or skill.error:
            continue
        if (problem := _tool_problems(skill)):
            # One bad declaration makes the whole skill broken, said plainly,
            # instead of quietly leaving a tool out or running it unscoped.
            skill.error = problem
            continue
        for spec in skill.tools:
            name = str(spec.get("name") or "").strip().lower()
            if not _NAME_RE.match(name):
                continue
            if name in claimed:
                print(f"[skills] tool {name!r} from {skill.name!r} skipped: "
                      f"the {claimed[name]!r} skill already provides it", flush=True)
                continue
            # Never let a skill shadow a built-in. Silently replacing
            # `send_email` or `run_shell` with a skill's own version would be a
            # capability swap the user never sees.
            if name in REGISTRY and name not in _registered:
                continue
            if not (spec.get("command") or spec.get("script")):
                continue

            props = spec.get("parameters") or {}
            if not isinstance(props, dict):
                props = {}
            schema = {
                "type": "object",
                # `required` is a JSON-Schema list on the object, not a flag
                # inside each property.
                "properties": {k: ({pk: pv for pk, pv in v.items() if pk != "required"}
                                   if isinstance(v, dict) else {"type": "string"})
                               for k, v in props.items()},
                "required": [k for k, v in props.items()
                             if isinstance(v, dict) and v.get("required")],
            }
            description = (str(spec.get("description") or name)
                           + f" (from the '{skill.name}' skill)")
            register(name, description, schema, category="skill_tool")(
                _make_runner(skill.path, spec, name, bundled=bool(skill.bundled)))
            _registered.add(name)
            claimed[name] = skill.name
            added.append(name)
    return added


@register(
    "use_skill",
    "Load the full instructions for one of the user's installed skills. The "
    "skills available are listed in your system prompt with a one-line "
    "description each; call this when a request matches one and its "
    "instructions weren't already loaded for you. Returns the skill's "
    "procedure, which you should then follow.",
    {"type": "object",
     "properties": {
         "name": {"type": "string", "description": "the skill's name"},
     },
     "required": ["name"]},
    category="assistant_read",
)
async def use_skill(name: str) -> str:
    from service.skills import all_skills, get
    skill = get((name or "").strip())
    if skill is None:
        # Case-insensitive second pass — the model reads the name off the
        # catalog, so a capitalization mismatch shouldn't be a dead end.
        wanted = (name or "").strip().lower()
        skill = next((s for s in all_skills().values() if s.name.lower() == wanted), None)
    if skill is None:
        available = ", ".join(sorted(all_skills())) or "(none installed)"
        return f"(no skill named {name!r}. Installed skills: {available})"
    if skill.error:
        return f"(the '{skill.name}' skill is broken: {skill.error})"
    if not skill.enabled:
        return f"(the '{skill.name}' skill is turned off — the user can enable it in Settings)"
    if skill.compose:
        # A compose style: Harbor adds it itself wherever a send or draft tool
        # is offered (skills.compose_style_block).
        return (f"(the '{skill.name}' skill is a style Harbor applies on its own to messages "
                "it writes for the user to send; there's nothing to load)")
    if skill.always:
        # A reply style, added to plain chat replies by Harbor itself; a
        # tool-using turn doesn't follow it (skills.Skill.always).
        return f"(the '{skill.name}' skill is a reply style Harbor applies to plain chat replies on its own; there's nothing to load)"
    return f"Instructions from the '{skill.name}' skill:\n\n{skill.body}"
