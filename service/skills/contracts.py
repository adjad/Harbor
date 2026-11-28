"""Checks for fixed workflow skills (inbox-cleanup, meeting-prep, week-review, file-tidy).

A workflow skill ships as instructions plus a `contract` under
`metadata.harbor` in its SKILL.md. The contract is the skill's declaration of
which of Harbor's own tools it may call, and which of those change anything:

    contract:
      read_only: true | false
      grants: none              # a skill never adds, widens or implies a grant
      folders: [...]            # only for skills that move files
      reads: [tool, ...]        # tools that only read
      preview: [tool, ...]      # tools with a no-change preview mode
      confirms: [tool, ...]     # tools that change something; each call is confirmed
      steps: [{tool, args, confirm?}, ...]   # the calls the skill makes, in order

A contract adds no authority. Every tool still runs through the policy engine
(service/safety/policy.py) and the standing-grant rules (service/safety/grants.py)
exactly as if the model had called it unprompted; `validate` only proves the
declaration matches what those rules do today, so a later change to a tool's
category or policy shows up as a failing check rather than a silent change in
what the skill may do.
"""
from __future__ import annotations

from pathlib import Path

from service.skills import Skill

_KNOWN_KEYS = {"read_only", "grants", "folders", "reads", "preview", "confirms", "steps"}
_STEP_CONFIRM = {"group", "plan"}

# A workflow skill may never call a tool in these categories: sending, deleting,
# running commands, editing the calendar, or anything an extension supplies.
_FORBIDDEN_CATEGORIES = frozenset({
    "email_send", "messages_send", "scheduled_send", "network_write", "fs_delete",
    "shell", "tool_authoring", "calendar_write", "skill_tool", "mcp_action",
    "messages_write", "email_draft", "messages_draft", "app_control",
})
# Folders a file-moving skill may name: the person's own, never home itself.
_USER_FOLDERS = ("Downloads", "Desktop", "Documents", "Pictures", "Movies", "Music")


def _names(contract: dict, key: str) -> list[str]:
    value = contract.get(key) or []
    return [str(v) for v in value] if isinstance(value, list) else []


def _inside_user_folders(path: str) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    home = Path.home().resolve()
    return any(resolved == home / f or (home / f) in resolved.parents for f in _USER_FOLDERS)


def _decide_in_mode(policy, category: str, args: dict, name: str, *, full_access: bool,
                    after_untrusted: bool):
    """policy.decide in a hypothetical mode (view-only off, Full access as given, no
    standing grants), never touching the person's own mode: policy.simulated_mode
    is per task, so a concurrent turn can't see it. `after_untrusted` applies the
    rule a real turn applies once an earlier step has read text Harbor did not
    write (policy.after_untrusted_read_decision)."""
    with policy.simulated_mode(read_only=False, full_access=full_access):
        decision = policy.decide(category, args, tool=name)
    if after_untrusted:
        decision = policy.after_untrusted_read_decision(name, category, args, decision)
    return decision


def _decide_under_full_access(policy, category: str, args: dict, name: str,
                              *, after_untrusted: bool = False):
    """policy.decide as if Full access were on (see _decide_in_mode)."""
    return _decide_in_mode(policy, category, args, name, full_access=True,
                           after_untrusted=after_untrusted)


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
    "array": (list,), "object": (dict,), "null": (type(None),),
}


def _type_ok(value: object, wanted: object) -> bool:
    names = wanted if isinstance(wanted, list) else [wanted]
    for name in names:
        kinds = _JSON_TYPES.get(str(name))
        if kinds is None:
            return True                          # a type this check doesn't know: not judged
        if isinstance(value, bool) and bool not in kinds:
            continue                             # True is an int to Python, not to JSON
        if isinstance(value, kinds):
            return True
    return False


def step_arg_problems(tool, args: dict, *, require_all: bool) -> list[str]:
    """How a step's `args` disagree with the tool's own JSON schema: a name the tool
    doesn't take, a value of the wrong type or outside its enum, and (with
    `require_all`) a required argument left out."""
    schema = tool.parameters if isinstance(getattr(tool, "parameters", None), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    problems: list[str] = []
    for key in sorted(args):
        spec = props.get(key)
        if spec is None:
            if props or schema.get("additionalProperties") is False:
                problems.append(f"{tool.name} takes no argument {key!r}")
            continue
        if not isinstance(spec, dict):
            continue
        value = args[key]
        if "type" in spec and not _type_ok(value, spec["type"]):
            problems.append(f"{tool.name}: {key}={value!r} is not of type {spec['type']}")
            continue
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            problems.append(f"{tool.name}: {key}={value!r} is not one of {enum}")
    if require_all:
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        missing = [str(k) for k in required if k not in args]
        if missing:
            problems.append(f"{tool.name} needs {', '.join(missing)}, which the step leaves out")
    return problems


def validate(skill: Skill, registry: dict | None = None) -> list[str]:
    """Problems with a workflow skill's contract; an empty list means it holds.

    `registry` defaults to the tool registry, which the caller must have
    populated (importing service.main registers every built-in tool).
    """
    from service.safety import policy
    if registry is None:
        from service.tools.registry import REGISTRY as registry

    problems: list[str] = []
    contract = skill.contract
    if skill.error:
        return [f"skill does not load: {skill.error}"]
    if not contract:
        return ["no contract declared"]
    if unknown := sorted(set(contract) - _KNOWN_KEYS):
        problems.append(f"unknown contract keys: {unknown}")
    if str(contract.get("grants")) != "none":
        problems.append("a workflow skill must declare `grants: none`")

    reads, preview, confirms = (_names(contract, k) for k in ("reads", "preview", "confirms"))
    read_only = bool(contract.get("read_only"))
    if read_only and (confirms or preview):
        problems.append("a read-only skill declares tools that change something")
    declared = set(reads) | set(preview) | set(confirms)

    for name in sorted(declared):
        tool = registry.get(name)
        if tool is None:
            problems.append(f"{name}: not a registered tool")
            continue
        if tool.category in _FORBIDDEN_CATEGORIES:
            problems.append(f"{name}: category {tool.category} is not allowed in a workflow skill")
        if name in reads and not (tool.category.endswith("_read")
                                  or tool.category in {"fs_read", "assistant_read"}):
            problems.append(f"{name}: declared as a read but its category is {tool.category}")
        if name in confirms and name not in reads and tool.category.endswith("_read"):
            problems.append(f"{name}: declared as confirmed but it is a read")
        if name in confirms and name in reads:
            problems.append(f"{name}: declared both as a read and as confirmed")

    steps = contract.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("no steps declared")
        steps = []
    used: set[str] = set()
    skips_card_in_full_access = False
    read_untrusted = False          # an earlier step put text Harbor did not write into the turn
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "tool" not in step:
            problems.append(f"step {i}: not a tool step")
            continue
        name = str(step["tool"])
        raw_args = step.get("args")
        if raw_args is not None and not isinstance(raw_args, dict):
            problems.append(f"step {i}: args must be a mapping")
            continue
        args = dict(raw_args or {})
        used.add(name)
        if name not in declared:
            problems.append(f"step {i}: {name} is not declared in reads/preview/confirms")
            continue
        tool = registry.get(name)
        if tool is None:
            continue
        asks = step.get("confirm")
        if asks is not None and asks not in _STEP_CONFIRM:
            problems.append(f"step {i}: confirm must be one of {sorted(_STEP_CONFIRM)}")
        if name in confirms and asks is None and not (name in preview and not args.get("confirm")):
            problems.append(f"step {i}: {name} changes something but the step has no confirm")
        # The arguments are checked against the tool's own schema. A step that
        # changes something must carry every required argument: its card and its
        # policy verdict are only meaningful for a call the tool would accept. A
        # read step's other arguments are the model's to fill in.
        problems += [f"step {i}: {p}" for p in
                     step_arg_problems(tool, args, require_all=bool(asks) or name in confirms)]
        # The real policy, in the default mode with no standing grant: a step
        # that asks must be a card, a step that does not must not change anything.
        decision = _decide_in_mode(policy, tool.category, args, name, full_access=False,
                                   after_untrusted=read_untrusted)
        if asks and decision.tier is not policy.Tier.CONFIRM:
            problems.append(f"step {i}: {name} is {decision.tier.value} under policy, not a confirm")
        if not asks and decision.tier is not policy.Tier.ALLOW:
            problems.append(f"step {i}: {name} is {decision.tier.value} under policy, not a plain read")
        # The same step under Full access, which skips some cards (email triage): the skill's
        # text must then say so rather than promise a card that never appears.
        full = _decide_under_full_access(policy, tool.category, args, name,
                                         after_untrusted=read_untrusted)
        if not asks and full.tier is not policy.Tier.ALLOW:
            problems.append(f"step {i}: {name} is {full.tier.value} under full access, not a plain read")
        if asks and full.tier is policy.Tier.DENY:
            problems.append(f"step {i}: {name} is refused under full access")
        if asks and full.tier is policy.Tier.ALLOW:
            skips_card_in_full_access = True
        if policy.turn_reads_untrusted(name, tool.category, args):
            read_untrusted = True
        if name == "organize_files":
            folder, dest = str(args.get("folder", "")), str(args.get("destination", ""))
            if not _inside_user_folders(folder):
                problems.append(f"step {i}: folder {folder!r} is outside the user's folders")
            if dest != "." and not _inside_user_folders(dest):
                problems.append(f"step {i}: destination {dest!r} is outside the user's folders")
    if unused := sorted(declared - used):
        problems.append(f"declared but never called by a step: {unused}")
    for folder in _names(contract, "folders"):
        if not _inside_user_folders(folder):
            problems.append(f"folder {folder!r} is not one of the user's own folders")
    if confirms and not contract.get("folders") and "organize_files" in confirms:
        problems.append("a file-moving skill must declare `folders`")

    body = skill.body
    if skips_card_in_full_access and "full access" not in " ".join(body.lower().split()):
        problems.append("a confirmed step runs without a card under full access, but the instructions "
                        "never say so (they must say it asks first unless the person gave full access)")
    for name in sorted(declared):
        if name not in body:
            problems.append(f"{name} is declared but the instructions never mention it")
    return problems
