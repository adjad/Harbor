"""Who this backend is, so the app can refuse to act on one that is not Harbor's own.

The app's backend port (8765) is hard-coded, and the app used to trust whatever
answered there: it ran every "send this email / create this event" request that arrived
over that port against the user's real Mail, Messages and Calendar. A sandbox backend
started on 8765 while the real app was open, or any other program that took the port,
could therefore make the real app act.

Two things make that refusable. The app proves the LISTENER is its own backend from
its launch receipt (held in the app's memory only, never on disk) and the kernel's record
of that process (pid and start time; that part does not trust this module, which only
echoes the receipt's launch nonce), and it
asks this endpoint which MODE the backend is in, so a backend that is Harbor's
code but running against a sandbox world is refused too. A sandbox backend also
refuses to start on a production port, as defence in depth.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from service.engine.manager import BUNDLED_PORT
from service.paths import MOE_DIR

# Ports the real app and its engine own. Nothing sandboxed may bind or proxy to them.
PRODUCTION_BACKEND_PORT = 8765
PRODUCTION_PORTS = frozenset({PRODUCTION_BACKEND_PORT, 8000, BUNDLED_PORT})


def is_sandbox() -> bool:
    """True for any backend that is not running against the user's real ~/.moe."""
    if os.environ.get("HARBOR_SANDBOX_HOME"):
        return True
    try:
        return MOE_DIR.resolve() != (Path.home() / ".moe").resolve()
    except OSError:
        return True  # cannot tell: never claim to be the real one


def mode() -> str:
    return "sandbox" if is_sandbox() else "production"


# The app passes each backend it launches a random nonce and records it in its launch
# receipt. Echoing it lets the app tell its own launch from another process.
LAUNCH_NONCE_ENV = "HARBOR_LAUNCH_NONCE"
# Never passed to anything this backend runs (a shell command, a skill script, an
# MCP server): the nonce marks only the backend the app launched, and it gates
# /resetharbor's restart pass in service/main.py.
# PYTHONNOUSERSITE is how the app keeps the per-user site-packages out of THIS process (a
# `.pth` file there would run inside the service). A command the person asks Harbor to run is
# their own Python and keeps its usual behaviour, so the setting stops here.
NEVER_INHERITED = frozenset({LAUNCH_NONCE_ENV, "PYTHONNOUSERSITE"})


# Provider API keys this backend may hold, named outright in case a name ever
# escapes the pattern below.
_PROVIDER_SECRETS = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY", "XAI_API_KEY", "COHERE_API_KEY", "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HARBOR_BRAVE_SEARCH_API_KEY",
    "HARBOR_OPENALEX_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
})
# Name parts that mark a secret: OPENAI_API_KEY, GH_TOKEN, DB_PASSWORD, MY_SECRET...
_SECRET_PARTS = frozenset({"KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
                           "PASSPHRASE", "CREDENTIAL", "CREDENTIALS", "PRIVATE"})
_SECRET_FRAGMENTS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "APIKEY", "API_KEY", "CREDENTIAL")


def is_secret_name(name: str) -> bool:
    """Whether an environment variable's name says it holds a secret."""
    upper = str(name).upper()
    if upper in _PROVIDER_SECRETS:
        return True
    if any(fragment in upper for fragment in _SECRET_FRAGMENTS):
        return True
    parts = [p for p in re.split(r"[^A-Z0-9]+", upper) if p]
    return any(p in _SECRET_PARTS for p in parts) or upper.endswith("KEY")


def child_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """``base`` (this process's environment by default) without NEVER_INHERITED and
    without any variable whose name marks a secret (is_secret_name): a command the
    model runs has no use for the backend's provider keys, and a planted one could
    print them."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items()
            if k not in NEVER_INHERITED and not is_secret_name(k)}


# What a skill's script may see of this process's environment: enough to find
# programs, its home and locale, and nothing else. A skill is code someone else
# wrote; the backend's settings and keys are none of its business.
_SKILL_ENV_NAMES = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TERM",
    "TMPDIR", "__CF_USER_TEXT_ENCODING",
})


def skill_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """The allowlisted environment for a skill tool's process (see _SKILL_ENV_NAMES,
    plus the LC_* locale settings). The caller adds the skill's own variables."""
    source = os.environ if base is None else base
    return {k: v for k, v in source.items()
            if (k in _SKILL_ENV_NAMES or k.startswith("LC_"))
            and k not in NEVER_INHERITED and not is_secret_name(k)}


def payload() -> dict:
    body = {"service": "harbor-backend", "mode": mode(), "pid": os.getpid()}
    nonce = os.environ.get(LAUNCH_NONCE_ENV)
    if nonce:
        body["launch_nonce"] = nonce
    return body


def bind_port(argv: list[str] | None = None) -> int | None:
    """The port this process was told to listen on (uvicorn's --port), if any."""
    args = list(sys.argv if argv is None else argv)
    for index, arg in enumerate(args):
        value = None
        if arg == "--port" and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith("--port="):
            value = arg.partition("=")[2]
        if value is not None:
            return int(value) if re.fullmatch(r"[0-9]{1,5}", value) else None
    return None


def refuse_sandbox_on_production_port(argv: list[str] | None = None,
                                      *, sandbox: bool | None = None) -> None:
    """A sandbox backend must never listen where the real app looks for its backend."""
    port = bind_port(argv)
    if (is_sandbox() if sandbox is None else sandbox) and port in PRODUCTION_PORTS:
        raise SystemExit(
            f"Refusing to start: this is a sandbox backend (not the real ~/.moe) and "
            f"port {port} belongs to the real Harbor. Use another port, for example 8775.")
