"""Built-in tools: shell + filesystem. Registered on import."""
from __future__ import annotations

import codecs
import contextvars
import os
import stat
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from service.tools.registry import register

MAX_OUTPUT = 12_000  # chars returned to the model

# Cap on pages actually extracted from a PDF. Bounds worst-case latency on a
# huge scanned report rather than the tool silently hanging — the model
# already gets everything up to MAX_OUTPUT chars, so a report over this many
# pages is telling the model to keep reading rather than losing content it
# would otherwise have seen (the char cap bites long before the page cap
# would on ordinary text-heavy PDFs; this exists for the pathological case).
_PDF_MAX_PAGES = 200


# One read_file call's window onto the text it extracts: (character offset,
# character limit). The format readers all end in _clip, so the window is applied
# there and every format pages the same way.
_MAX_PAGE_CHARS = 48_000
_PAGE: contextvars.ContextVar[tuple[int, int]] = contextvars.ContextVar(
    "read_file_page", default=(0, MAX_OUTPUT))


def _clip(s: str) -> str:
    offset, limit = _PAGE.get()
    if offset == 0 and len(s) <= limit:
        return s
    if offset >= len(s):
        return f"(offset {offset:,} is past the end: the text is {len(s):,} characters long.)"
    end = offset + limit
    body = s[offset:end]
    head = f"[characters {offset:,}-{offset + len(body):,} of {len(s):,}]\n" if offset else ""
    if end >= len(s):
        return head + body
    return (head + body + f"\n…[truncated {len(s) - end} chars; call read_file again with "
            f"offset={end} for the next page]")


def _detect_utf16(head: bytes) -> str | None:
    """The codec for UTF-16 text (a BOM, or ASCII-range text stored two bytes a
    character), or None. NUL bytes alone do not make a file binary."""
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    pairs = len(head) // 2
    if pairs < 4:
        return None
    even = sum(1 for b in head[0:pairs * 2:2] if b == 0)
    odd = sum(1 for b in head[1:pairs * 2:2] if b == 0)
    if odd >= pairs * 0.3 and even <= pairs * 0.05:
        codec = "utf-16-le"
    elif even >= pairs * 0.3 and odd <= pairs * 0.05:
        codec = "utf-16-be"
    else:
        return None
    try:
        text = head[:pairs * 2].decode(codec)
    except UnicodeDecodeError:
        return None
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    return codec if text and printable / len(text) >= 0.95 else None


# macOS blocks Documents / Desktop / Downloads / iCloud Drive per-app under
# TCC, and the block surfaces as a bare PermissionError from os.listdir/open.
# Unhandled, that propagates as a tool exception and the model reports it as
# though the folder were empty or missing — which is how "only a small portion
# of files can be accessed by Harbor" looks from the outside. Name the real cause
# and the exact fix instead: this is a grant the user has to give in System
# Settings; no retry, different path, or shell fallback can work around it.
def _tcc_message(p: Path, action: str) -> str:
    return (f"(macOS blocked {action} {p} — Harbor does not have permission for "
            "that folder. Grant it in System Settings > Privacy & Security > "
            "Full Disk Access by adding Harbor, then quit and reopen Harbor. "
            "Tell the user this; do NOT retry, and do NOT try to reach it with "
            "run_shell — the same block applies there.)")


def _missing_dir_message(p: Path) -> str:
    """A dead-end path, answered with the nearest folder that DOES exist.

    MEASURED FAILURE (2026-08-18). Asked to "reorganize my harbor debug logs",
    the model guessed `~/Harbor/logs`, got "(not a directory: …)", guessed
    `~/Harbor`, got the same, ran two `run_shell` finds, tried `~/DebugLogs`,
    found it empty, and concluded "no files to reorganize — it looks like there
    are no log files to sort". The logs were in `~/Downloads` the whole time.
    Five tool calls, a wrong answer, and it never once listed a real folder.

    SYSTEM already tells it what to do here — "If a path turns out not to
    exist, call `list_dir` on the PARENT folder to see the real names — do NOT
    switch to run_shell" — and it did exactly the forbidden thing. So walk up to
    the nearest existing ancestor and hand back that listing directly: the
    model does not have to decide to do it, and the names it needs are already
    on screen. Same principle as _wrong_account_path and timeranges.BadPeriod —
    a failure should teach the caller, not just report.

    NOT flagged as "(error" (unlike _wrong_account_path): this return carries
    real, useful directory content, so it should stay eligible for the
    narration fallback rather than being filtered out of it.
    """
    anc = p.parent
    while anc != anc.parent and not anc.is_dir():
        anc = anc.parent
    head = f"(no such directory: {p})"
    if not anc.is_dir():
        return head
    try:
        entries = sorted(os.listdir(anc))
    except PermissionError:
        # macOS reports a path inside a blocked folder as missing; the nearest
        # real ancestor is the folder it is blocking.
        return _tcc_message(anc, "listing")
    except OSError:
        return head
    shown = [e for e in entries if not e.startswith(".")][:60]
    listing = "\n".join(shown) or "(empty)"
    return _clip(f"{head}\n\nThe nearest folder that DOES exist is `{anc}` — "
                 f"here is what is actually in it. Pick the right name from "
                 f"this list and call list_dir again; do NOT guess another "
                 f"path and do NOT switch to run_shell:\n{listing}")


def _wrong_account_path(path: str) -> str | None:
    """A corrective message when `path` names a `/Users/<account>` that isn't
    the real one — otherwise None.

    MEASURED FAILURE (2026-08-18). Asked to "reorganize my files" — a file
    request that names no folder — Ling-3.0-tiny built `/Users/SamRivera` out
    of the user's EMAIL ADDRESS, which the identity block puts in every agent
    system prompt (see agent/loop.py's identity_hint). It then tried
    `/Users/samrivera` and `/Users/Sam`. 10 runs out of 10.

    SYSTEM already forbids exactly this — "you do not know the user's account
    name, so `/Users/<name>/…` is a guess and is usually wrong" — and the model
    ignores it; adding a second, stronger rule right next to the email only took
    it from 10/10 to 2/10, at 2.4x the tool calls. A prompt cannot close this.
    So the fix is deterministic and lives here, where the answer is KNOWN:
    Path.home() is ground truth, and no model has to infer it.

    Corrective rather than silent: the old return, "(not a directory: …)",
    states the failure without naming the fix, so the model just guesses
    another spelling of the username and burns its remaining steps. This names
    the exact replacement path, so a retry lands on the first attempt. Same
    principle tools/timeranges.BadPeriod already states: "a bad argument should
    teach the caller the vocabulary rather than just failing".

    Deliberately NOT auto-rewriting the path. Silently redirecting one location
    to a different one is the kind of guess that, on move_path or write_file,
    would act on a file the user never named.
    """
    p = (path or "").strip()
    if not p.startswith("/Users/"):
        return None
    parts = p.split("/")                      # ['', 'Users', '<account>', ...]
    if len(parts) < 3 or not parts[2]:
        return None
    account = parts[2]
    # /Users/Shared is a real, account-independent macOS location.
    if account == Path.home().name or account == "Shared":
        return None
    rest = "/".join(parts[3:]).rstrip("/")
    suggested = f"~/{rest}" if rest else "~"
    # Leading "(error" on purpose: registry.is_tool_error keys off exactly that
    # marker, and the agent loop uses it to keep failures OUT of clean_results —
    # the set _merge_results renders when the model produces no answer of its
    # own. Without the marker this text was reaching the USER verbatim (measured
    # 2026-08-18: 6 of 10 fallback answers opened with it). The model still
    # sees it either way; tool results are fed back regardless of this flag.
    return (f"(error: no such path: {p} — `{account}` is NOT the user's account "
            f"name. Their email address is not their username, and you cannot "
            f"guess the account name. Use `{suggested}` instead: `~` always means "
            f"the user's home folder. Retry this call with `{suggested}`.)")


@register(
    "run_shell",
    # The "don't use this, use X instead" list used to name ~15 tools
    # individually. That was written when every request saw the whole registry;
    # with routes scoped it became actively WRONG on the narrow ones — on the
    # ambiguous core route it told the model to prefer get_volume, set_wifi (now toggle_setting),
    # spotify and a dozen others that are not on the table there, which is the
    # same trap as pointing at a deleted tool. Naming the CATEGORIES and
    # deferring to "if it's in your tool list" is correct on every route and
    # about half the size. The two facts that are not guessable — pmset's log
    # is not a health metric, and the keyboard backlight isn't scriptable at
    # all — are kept, because those are exactly where the model invents a
    # confident wrong answer.
    "Run a shell command on the user's Mac and return its stdout/stderr. "
    "Use for inspecting and operating the system. Prefer read-only commands; "
    "implicit read-only access supports only validated pwd/echo/whoami/uname/"
    "ls/cat/head/tail/wc arguments, with no shell operators or expansions. "
    "Other commands follow the user's configured permissions and confirmation rules. "
    "If a dedicated tool for the job is in your tool list — files and folders, "
    "device settings, clipboard, calendar/reminders, mail, messages, notes, "
    "media, or live web "
    "data — call that instead: it is faster, more reliable, and some have no "
    "working shell equivalent (pmset's output is PM event history, NOT battery "
    "health; the keyboard backlight is not scriptable at all).",
    {"type": "object",
     "properties": {"cmd": {"type": "string", "description": "the shell command"}},
     "required": ["cmd"]},
    category="shell",
)
def run_shell(cmd: str) -> str:
    from service.router.model_led import BuiltinCompletion
    try:
        from service.safety.policy import Tier, decide

        # Recheck at execution time (also covers direct calls and a mode change
        # while a call was queued). CONFIRM is fulfilled by the caller; explicit
        # grants/full access keep the existing shell semantics.
        decision = decide("shell", {"cmd": cmd}, tool="run_shell")
        if decision.tier is Tier.DENY:
            return f"(error: blocked by safety policy: {decision.reason})"
        targets = getattr(decision, "delete_targets", None)
        if targets is not None:
            return _run_plain_delete(cmd, targets)
        note = ""
        if decision.shell_argv is not None:
            p = subprocess.run(
                decision.shell_argv, shell=False, capture_output=True, text=True,
                timeout=120, cwd=str(Path.home()), stdin=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        else:
            from service.identity import child_environment
            from service.safety import shell_sandbox
            # The same `/bin/sh -c` that shell=True runs, under the kernel's write guard for
            # Harbor's own folder: the policy above reads the command's text, and a name built
            # at run time is not in the text.
            argv, contained = shell_sandbox.contain(["/bin/sh", "-c", cmd])
            p = subprocess.run(argv, shell=False, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL,
                               timeout=120, cwd=str(Path.home()), env=child_environment())
            note = shell_sandbox.explain(contained, p.returncode, p.stderr)
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        out = _clip(out.strip() or f"(exit {p.returncode}, no output)")
        return BuiltinCompletion(out + ("\n" + note if note else ""),
                                 tool_name="run_shell", completion_code=p.returncode,
                                 phase="read" if decision.shell_argv is not None else "mutation")
    except subprocess.TimeoutExpired:
        return "(command timed out after 120s)"
    except Exception as e:  # noqa: BLE001
        return f"(error running command: {e})"


def _run_plain_delete(cmd: str, judged) -> str:
    """One plain delete of absolute paths (policy.plain_delete_targets).

    `judged` is what run_shell's own decide() found a moment earlier, not
    what the card the person approved showed: the approval flow hands
    run_shell only `cmd`, so the card's paths are not available here. Each
    path is resolved and checked again immediately before the delete, and
    the delete is refused if any path now leads somewhere else than in
    `judged` (a link swapped in between) or no longer passes the checks. It
    then runs the decoded words as a fixed argv with no shell, so quoting
    cannot be read two ways. What remains: the moments between this last
    check and rm opening each path; and a change between the card and
    run_shell's decide() that still passes every check (a link moved from one
    ordinary folder to another) is deleted without a new card."""
    from service.router.model_led import BuiltinCompletion
    from service.safety.policy import plain_delete_argv
    again = plain_delete_argv({"cmd": cmd})
    if again is None or again[1] != tuple(judged):
        return ("(error: not run: a path in this delete changed after it was checked, or now "
                "resolves to a folder Harbor does not delete, so nothing was deleted. Check the "
                "paths and ask again if it should still be deleted.)")
    argv, _ = again
    from service.safety import shell_sandbox
    argv, contained = shell_sandbox.contain(list(argv))     # the same write guard as any command
    p = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=120,
                       cwd=str(Path.home()), stdin=subprocess.DEVNULL,
                       env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
    out = _clip(out.strip() or f"(exit {p.returncode}, no output)")
    if note := shell_sandbox.explain(contained, p.returncode, p.stderr):
        out += "\n" + note
    return BuiltinCompletion(out, tool_name="run_shell", completion_code=p.returncode, phase="mutation")


def _run_private_shell_read(cmd: str) -> str:
    """Host-only dispatch: validated fixed argv even under full access/grants."""
    from service.router.model_led import BuiltinCompletion
    from service.safety.policy import Tier, decide, read_only_shell_argv
    # Revalidate in the worker, immediately before dispatch. A grant may
    # narrow or deny a read, but cannot turn this path into a general shell.
    argv = read_only_shell_argv(cmd)
    if argv is None:
        return "(error: private context permits only validated local shell reads.)"
    decision = decide("shell", {"cmd": cmd}, tool="run_shell")
    if decision.tier is Tier.DENY:
        return f"(error: blocked by safety policy: {decision.reason})"
    try:
        p = subprocess.run(argv, shell=False, capture_output=True, text=True,
                           timeout=120, cwd=str(Path.home()), stdin=subprocess.DEVNULL,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        return BuiltinCompletion(_clip(out.strip() or f"(exit {p.returncode}, no output)"),
                                 tool_name="run_shell", completion_code=p.returncode, phase="read")
    except subprocess.TimeoutExpired:
        return "(command timed out after 120s)"
    except Exception as e:
        return f"(error running command: {e})"


# Capture callable and code identities at trusted module initialization.
# Registry metadata, names, attributes and copied function bodies cannot grant
# the private read exception. This is host integrity, not hostile-Python isolation.
_PRIVATE_SHELL_DISPATCH = (run_shell, run_shell.__code__,
                           _run_private_shell_read, _run_private_shell_read.__code__)


def _read_pdf(p: Path) -> str:
    """Extract real text from a PDF via pypdf.

    Reported live: read_file used to just call p.read_text() on every path, so
    a PDF handed back its raw bytes — compressed FlateDecode streams and object
    headers, not the document's actual words (e.g. "x\\x9c}\\xdb\\x8e\\x1c..."
    where the real content should be). The model wasn't failing to summarize;
    it was asked to summarize noise and had no way to say so, which is why
    "Summarize a PDF" stalled rather than erroring.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(p))
    except PdfReadError as e:
        return f"(couldn't parse {p} as a PDF: {e})"

    if reader.is_encrypted:
        # Many PDFs are "encrypted" only to restrict editing or printing: they
        # open with an empty user password. `is_encrypted` stays True after a
        # successful decrypt, so the verdict is decrypt()'s own result (0 means
        # the password did not fit), never a second look at the flag.
        try:
            opened = int(reader.decrypt(""))
        except Exception as e:  # noqa: BLE001 — a missing AES backend is not "wrong password"
            if "cryptography" in str(e).lower() or "pycryptodome" in str(e).lower():
                return (f"({p} is encrypted with a method this Harbor build can't open. "
                        "Tell the user plainly.)")
            opened = 0
        if not opened:
            return f"({p} is password-protected — Harbor can't read it without the password.)"

    pages = reader.pages[:_PDF_MAX_PAGES]
    texts = [(page.extract_text() or "").strip() for page in pages]
    text = "\n\n".join(t for t in texts if t)
    if not text:
        # extract_text() returns "" for a page with no text LAYER — a scanned
        # photo/print PDF with no OCR, not a Harbor failure. This used to point at
        # describe_image; that tool is gone with the vision path, so say plainly
        # that it can't be read. Naming a tool that no longer exists is worse
        # than admitting the limit: the model would call it, get "not
        # available", and report that as a Harbor malfunction.
        return (f"({p} has no extractable text — it is a scanned/image-only PDF "
                "with no text layer. Harbor cannot read scanned documents. Tell "
                "the user this plainly; do not retry with another tool.)")
    note = (f"[showing first {_PDF_MAX_PAGES} of {len(reader.pages)} pages]\n\n"
           if len(reader.pages) > _PDF_MAX_PAGES else "")
    return _clip(note + text)


def _read_docx(p: Path) -> str:
    """Extract paragraph + table text from a Word document.

    A .docx is a ZIP of XML parts, so read_file's old p.read_text() returned
    ZIP central-directory bytes — same failure as the PDF case, different
    binary format. python-docx unpacks it properly instead.
    """
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError

    try:
        doc = Document(str(p))
    except PackageNotFoundError:
        return f"(couldn't parse {p} as a .docx file — it may be corrupt or not a real Word document)"

    parts = [para.text for para in doc.paragraphs if para.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    if not text:
        return f"({p} has no readable text — it may be empty or contain only images.)"
    return _clip(text)


def _read_xlsx(p: Path) -> str:
    """Extract cell values from every sheet, as plain rows.

    Formulas are read as their CACHED VALUE (data_only=True) — the model wants
    what the spreadsheet currently shows, not the formula text, and Excel
    stores both in the file so this needs no evaluation of our own.
    """
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        wb = load_workbook(str(p), data_only=True, read_only=True)
    except InvalidFileException:
        return f"(couldn't parse {p} as an .xlsx file — it may be corrupt or not a real Excel workbook)"

    sheets = []
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                sheets.append(f"[Sheet: {name}]\n" + "\n".join(rows))
    finally:
        wb.close()   # a read-only workbook keeps the file open until closed
    text = "\n\n".join(sheets)
    if not text:
        return f"({p} has no data in any sheet.)"
    return _clip(text)


def _pptx_shape_lines(shapes) -> list[str]:
    """Text of each shape, descending into groups and reading table cells."""
    lines: list[str] = []
    for shape in shapes:
        if getattr(shape, "shapes", None) is not None and not getattr(shape, "has_text_frame", False):
            lines.extend(_pptx_shape_lines(shape.shapes))      # a group
            continue
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame \
                and shape.text_frame.text.strip():
            lines.append(shape.text_frame.text)
    return lines


def _read_pptx(p: Path) -> str:
    """Extract text from every shape/frame, slide by slide, plus speaker notes."""
    from pptx import Presentation
    from pptx.exc import PackageNotFoundError as PptxPackageNotFoundError

    try:
        deck = Presentation(str(p))
    except PptxPackageNotFoundError:
        return f"(couldn't parse {p} as a .pptx file — it may be corrupt or not a real PowerPoint file)"

    slides = []
    for i, slide in enumerate(deck.slides, 1):
        lines = _pptx_shape_lines(slide.shapes)
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                lines.append(f"[speaker notes] {notes}")
        if lines:
            slides.append(f"[Slide {i}]\n" + "\n".join(lines))
    text = "\n\n".join(slides)
    if not text:
        return f"({p} has no readable text — it may contain only images/diagrams.)"
    return _clip(text)


# read_file looks at the start of a file only. PDF and Office readers load the
# whole document, so they get a size cap instead of a prefix.
_TEXT_READ_BYTES = MAX_OUTPUT * 4
_MAX_TEXT_PAGE_BYTES = 16 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 100 * 1024 * 1024


def _too_large_message(p: Path, size: int) -> str:
    return (f"({p} is {size / 1_000_000:,.0f} MB, too large for Harbor to open "
            "as a document. Tell the user this plainly; do not retry.)")


_STRUCTURED_READERS = {
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
    ".pptx": _read_pptx,
}


@register(
    "read_file",
    "Read and return the contents of a file — plain text, or a PDF/.docx/"
    ".xlsx/.pptx (real text is extracted, not raw bytes). Scanned/image-only "
    "PDFs and image files CANNOT be read by this tool; say so plainly rather "
    "than trying another tool, and tell the person they can attach the picture "
    "in Ask for Harbor to look at it. Older .doc/.xls/.ppt (no 'x') "
    "are NOT supported — tell the user to re-save as the newer format.",
    {"type": "object",
     "properties": {"path": {"type": "string"},
                    "offset": {"type": "integer",
                               "description": "character to start from (default 0); a truncated read names the offset of its next page"},
                    "limit": {"type": "integer",
                              "description": "characters to return (default 12000, at most 48000)"}},
     "required": ["path"]},
    category="fs_read",
)
def read_file(path: str, offset: int = 0, limit: int = MAX_OUTPUT) -> str:
    try:
        offset = max(0, int(offset or 0))
        limit = min(_MAX_PAGE_CHARS, max(1, int(limit or MAX_OUTPUT)))
    except (TypeError, ValueError):
        return "(offset and limit must be whole numbers)"
    token = _PAGE.set((offset, limit))
    try:
        return _read_file_page(path, offset, limit)
    finally:
        _PAGE.reset(token)


def _read_file_page(path: str, offset: int, limit: int) -> str:
    if (msg := _wrong_account_path(path)):
        return msg
    p = Path(path).expanduser()
    # stat() BEFORE exists(). Path.exists() swallows OSError and returns False,
    # so a file macOS is merely blocking is indistinguishable from one that
    # isn't there — and "(no such file: ~/Documents/notes.txt)" sends the model
    # off inventing other paths for a file that exists and is simply
    # unreadable. See _tcc_message.
    try:
        p.stat()
    except PermissionError:
        return _tcc_message(p, "reading")
    except FileNotFoundError:
        # macOS reports a path inside a BLOCKED folder as ENOENT, not EPERM —
        # it hides whether the file exists at all. So a bare "no such file"
        # here can equally mean "you aren't allowed to know". Only trust it
        # when the parent folder is actually readable.
        try:
            os.listdir(p.parent)
        except PermissionError:
            return _tcc_message(p, "reading")
        except OSError:
            pass
        return f"(no such file: {p})"
    except OSError:
        pass
    if not p.exists():
        return f"(no such file: {p})"
    if p.is_dir():
        return f"({p} is a directory, not a file — use list_dir instead)"
    # Read a bounded prefix, never the whole file: a multi-GB movie or log
    # would otherwise be allocated (twice, with the decode) next to the model.
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    try:
        with p.open("rb") as fh:
            # A later page needs more of the file than the first one does.
            raw = fh.read(min(_MAX_TEXT_PAGE_BYTES, max(_TEXT_READ_BYTES, (offset + limit) * 4)))
    except PermissionError:
        return _tcc_message(p, "reading")
    except Exception as e:  # noqa: BLE001
        return f"(error reading {p}: {e})"
    head = raw[:8192]

    if p.suffix.lower() == ".pdf" or head.startswith(b"%PDF-"):
        if size > _MAX_DOCUMENT_BYTES:
            return _too_large_message(p, size)
        return _read_pdf(p)
    if p.suffix.lower() in _STRUCTURED_READERS:
        if size > _MAX_DOCUMENT_BYTES:
            return _too_large_message(p, size)
        try:
            return _STRUCTURED_READERS[p.suffix.lower()](p)
        except Exception as e:  # noqa: BLE001 — malformed OOXML must report, not crash the turn
            return f"(error reading {p}: {e})"
    if p.suffix.lower() in (".doc", ".xls", ".ppt"):
        # The legacy binary Office formats (pre-2007, no trailing 'x') are a
        # completely different container (OLE2 compound file, not ZIP+XML) —
        # none of the three readers above can open one, and without this check
        # it would fall through to the NUL-byte guard below and get the same
        # unhelpful "is a binary file" message as a JPEG. Naming the real
        # reason (old format) tells the user exactly what to do about it.
        return (f"({p} is an older Office format (.{p.suffix.lstrip('.')}) that "
                "Harbor can't read — ask the user to re-save it as the newer "
                f".{p.suffix.lstrip('.')}x format in Word/Excel/PowerPoint.)")

    # Same NUL-byte heuristic git/diff use to flag a binary file — genuine
    # text essentially never contains one. Catches every OTHER format Harbor has
    # no reader for (images, .docx/.pptx/.xlsx, executables, archives) so those
    # report plainly instead of repeating the PDF bug in a new shape: raw bytes
    # decoded with errors="replace" LOOKS like a string, and nothing before
    # this would have stopped it from being handed to the model as if it were
    # the document's actual content.
    utf16 = _detect_utf16(head) if b"\x00" in head or head[:2] in (b"\xff\xfe", b"\xfe\xff") else None
    if b"\x00" in head and utf16 is None:
        kind = p.suffix.lstrip(".") or "binary"
        return (f"({p} is a {kind} file, not text — Harbor can't read its contents. "
                "This tool cannot read image files: tell the user this plainly and "
                "that they can attach the picture in Ask for Harbor to look at it; "
                "do not retry with another tool.)")

    codec = utf16 or "utf-8"
    if size <= len(raw):
        return _clip(raw.decode(codec, errors="replace"))
    # Only the start was read. The incremental decoder drops a multi-byte
    # character cut at the end instead of turning it into a replacement mark.
    text = codecs.getincrementaldecoder(codec)("replace").decode(raw, final=False)
    end = offset + limit
    if offset >= len(text):
        return (f"(offset {offset:,} is past the part of the file Harbor reads "
                f"({len(text):,} characters of a {size:,}-byte file).)")
    window = text[offset:end]
    head_note = f"[characters {offset:,}-{offset + len(window):,}]\n" if offset else ""
    if end < len(text):
        return (head_note + window + f"\n…[truncated: the file is {size:,} bytes; call read_file "
                f"again with offset={end} for the next page]")
    return (head_note + window
            + f"\n…[truncated: the file is {size:,} bytes; this is as far as Harbor reads it]")


# Description length is load-bearing here, not padding. At its original 32
# characters ("List the entries in a directory.") this tool lost the files
# route to run_shell, whose own description runs 554 characters and reads as a
# pitch. Measured 2026-08-09 over 5 reps of "List the files in my Downloads
# folder.": list_dir alone 2/5, run_shell involved 3/5, worst case 4 run_shell
# calls / 5 model steps / 62s. Same failure class docs/STABILITY_PLAN.md already
# records for an over-long run_shell description (4/4 wrong before, 0/4 after)
# — this is its mirror image, a tool too terse to be chosen.
#
# The `~` instruction is the second half of the fix, and it is what actually
# broke the worst run: the model does not know the account name and guessed
# `/Users/samrivera/Downloads` (wrong case — survived only because macOS is
# case-insensitive) and `/Downloads` (did not exist, which is what sent it to
# run_shell). expanduser() below has always handled `~`; nothing said so.
@register(
    "list_dir",
    "List the files and folders inside a directory on the user's Mac — use "
    "this for 'what's in my Downloads/Desktop/Documents', 'list the files "
    "in X', or to check whether a file exists before reading it. Write the "
    "path with a leading `~` (e.g. `~/Downloads`, `~/Desktop/Projects`): you "
    "do NOT know the user's account name, so an absolute `/Users/<name>/...` "
    "path is a guess and will often be wrong. Pair with read_file to open "
    "something you found here. Pass `recursive=true` to see the WHOLE tree "
    "(subfolders and their contents) in one call instead of listing each "
    "subfolder one at a time — use this whenever the job needs to know what's "
    "inside nested folders, e.g. reorganizing/sorting files that might be "
    "scattered across several subfolders. Folders end with `/` in the output. "
    "Pass `details=true` for each entry's size (a folder's is the total of "
    "everything in it) and last-modified date, e.g. 'what's taking up space "
    "in Downloads' or 'which folder did I change last'; `sort` orders them.",
    {"type": "object",
     "properties": {
         "path": {"type": "string", "description": "directory path, e.g. ~/Downloads"},
         "recursive": {"type": "boolean",
                       "description": "list subfolders' contents too, not just this "
                                      "directory's immediate entries (default false)"},
         "max_depth": {"type": "integer",
                       "description": "how many folder levels deep to descend when "
                                      "recursive is true (default 3)"},
         "details": {"type": "boolean",
                     "description": "show each entry's size and last-modified date "
                                    "(with recursive, every entry in the tree; default false)"},
         "sort": {"type": "string", "enum": ["name", "size", "modified"],
                  "description": "with details: order by name (default), largest first, "
                                 "or most recently modified first"},
     },
     "required": ["path"]},
    category="fs_read",
)
def list_dir(path: str, recursive: bool = False, max_depth: int = 3,
             details: bool = False, sort: str = "name") -> str:
    if (msg := _wrong_account_path(path)):
        return msg
    p = Path(path).expanduser()
    if not p.is_dir():
        return _missing_dir_message(p)
    if details:
        depth = max(1, min(int(max_depth or 3), 8)) if recursive else 1
        return _list_dir_details(p, sort, depth)
    if not recursive:
        try:
            entries = sorted(os.listdir(p))
        except PermissionError:
            return _tcc_message(p, "listing")
        return _clip("\n".join(entries) or "(empty)")

    max_depth = max(1, min(int(max_depth or 3), 8))
    lines: list[str] = []
    root_depth = len(p.parts)
    # onerror: os.walk swallows errors silently by default, so a blocked
    # subfolder would just be absent from the listing with nothing said.
    _blocked: list[str] = []
    used, cut = 0, False
    for dirpath, dirnames, filenames in os.walk(
            p, onerror=lambda e: _blocked.append(str(getattr(e, "filename", "")))):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames.clear()  # os.walk stops descending past a cleared list
            continue
        dirnames.sort()
        rel = Path(dirpath).relative_to(p)
        prefix = "" if rel == Path(".") else f"{rel}/"
        for name in dirnames:
            lines.append(f"{prefix}{name}/")
            used += len(lines[-1]) + 1
        for name in sorted(filenames):
            lines.append(f"{prefix}{name}")
            used += len(lines[-1]) + 1
        if used > MAX_OUTPUT:
            # The model only sees MAX_OUTPUT chars, so walking on (into
            # Library or node_modules) would only burn time and memory.
            cut = True
            break
    if _blocked and not lines:
        return _tcc_message(p, "listing")
    # First, so a long listing's truncation can never cut it off.
    notice = (f"…[{len(_blocked)} subfolder(s) skipped — macOS denied "
              "access; see Full Disk Access in System Settings]\n") if _blocked else ""
    if cut:
        return notice + "\n".join(lines)[:MAX_OUTPUT - len(notice)] + (
            "\n…[listing cut: this folder is large. List a subfolder, or use "
            "find_files, to see more]")
    return notice + _clip("\n".join(lines) or "(empty)")


# A folder's size is a walk of everything under it, so the walks of one
# listing share a budget: past it, sizes are marked as at least that much.
_DETAIL_BUDGET_S = 3.0
_DETAIL_MAX_FILES = 200_000


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _list_dir_details(p: Path, sort: str, max_depth: int = 1) -> str:
    """One line per entry of `p` down to `max_depth` levels: name, size, last
    modified (2026-10-29 Ask eval: folder sizes and dates had nothing to read).
    A folder's size is everything under it, at any depth, from one walk of the
    tree on one budget; a folder the walk didn't finish is "at least" its size."""
    deadline = time.monotonic() + _DETAIL_BUDGET_S
    totals: dict[Path, int] = {}
    partial: set[Path] = set()
    rows: list[tuple[Path, int, float, bool]] = []
    root = Path(".")

    def unfinished(rel: Path) -> None:
        partial.add(rel)
        partial.update(rel.parents)

    seen, stack, stopped = 0, [root], False
    while stack and not stopped:
        rel = stack.pop()
        try:
            it = os.scandir(p / rel)
        except PermissionError:
            if rel == root:
                return _tcc_message(p, "listing")
            unfinished(rel)
            continue
        except OSError:
            unfinished(rel)
            continue
        with it:
            for entry in it:
                seen += 1
                if seen > _DETAIL_MAX_FILES or time.monotonic() > deadline:
                    stopped = True
                    unfinished(rel)
                    break
                child = rel / entry.name
                try:
                    info = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    unfinished(rel)
                    continue
                if is_dir:
                    stack.append(child)
                    totals.setdefault(child, 0)
                else:
                    for folder in child.parents:
                        totals[folder] = totals.get(folder, 0) + info.st_size
                if len(child.parts) <= max_depth:
                    rows.append((child, 0 if is_dir else info.st_size, info.st_mtime, is_dir))
    for rel in stack:
        unfinished(rel)
    if not rows:
        return "(empty)"
    sized = [(f"{rel}/" if is_dir else str(rel), totals.get(rel, 0) if is_dir else size, mtime,
              not (is_dir and rel in partial))
             for rel, size, mtime, is_dir in rows]
    order = (sort or "name").strip().lower()
    if order == "size":
        sized.sort(key=lambda r: r[1], reverse=True)
    elif order == "modified":
        sized.sort(key=lambda r: r[2], reverse=True)
    else:
        sized.sort(key=lambda r: r[0].casefold())
    lines = [f"{name}  {'at least ' if not complete else ''}{_human_size(size)}  "
             f"modified {datetime.fromtimestamp(mtime).strftime('%b %-d, %Y %-I:%M %p')}"
             for name, size, mtime, complete in sized]
    if any(not r[3] for r in sized):
        lines.append("(\"at least\": that folder was too big to total in time, or macOS refused part of it.)")
    return _clip("\n".join(lines))


@register(
    "write_file",
    "Write text to a file, creating or overwriting it.",
    {"type": "object",
     "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
     "required": ["path", "content"]},
    category="fs_write",
)
def write_file(path: str, content: str) -> str:
    if (msg := _wrong_account_path(path)):
        return msg
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        # Overwriting keeps a file's mode; new text in a file that was executable
        # must not be left runnable (candidate review #2 of the 2026-11-25 pentest).
        mode = p.stat().st_mode
        if mode & 0o111:
            os.chmod(p, stat.S_IMODE(mode) & ~0o111)
        return f"wrote {len(content)} chars to {p}"
    except Exception as e:  # noqa: BLE001
        return f"(error writing {p}: {e})"


@register(
    "move_path",
    "MOVE or RENAME a file or folder — this is how you reorganize, tidy up, or "
    "file things away. Use this for ANY request to move, rename, sort, group, "
    "organize or reorganize files, one call per item. Creates the destination "
    "folder automatically, so you do NOT need a separate mkdir. Do NOT use "
    "run_shell with mv/rm for this: moving is the whole job, and deleting a "
    "folder is never part of it — a folder becomes empty because its contents "
    "were moved out, and an empty folder is harmless to leave behind.",
    {"type": "object",
     "properties": {
         "source": {"type": "string", "description": "the file or folder to move, e.g. ~/Downloads/log.json"},
         "destination": {"type": "string",
                         "description": "where it should end up. A path ending in '/' or an "
                                        "existing folder moves the item INTO that folder keeping "
                                        "its name; otherwise it is the item's new full path."},
     },
     "required": ["source", "destination"]},
    category="fs_write",
)
def move_path(source: str, destination: str) -> str:
    """Reorganizing had no primitive, so the model reached for `run_shell`.

    WHY THIS TOOL EXISTS (2026-08-16). The file/document route offers
    read_file / list_dir / write_file / run_shell — nothing that can move
    anything. Asked to "reorganize the harbor debug logs in my downloads folder",
    the agent said so itself in its first step ("I do not have a specific 'file
    move' or 'reorganize' tool"), fell back to shell, created destinations
    inside the source folder, then ran `rm -rf` on the sources without ever
    running a single `mv`. Three weeks of logs were destroyed.

    A confirmation card now stands in front of that specific shell shape (see
    policy._DESTRUCTIVE_SHELL), but a card is a last line of defence. This is
    the first one: give the job a primitive that CANNOT delete, so the model
    never has a reason to reach past it. Every failure mode of a move is
    recoverable — worst case something lands in the wrong folder.

    Refuses to overwrite an existing destination: silently clobbering a file
    during a "tidy up" is the same class of invisible loss this exists to
    prevent, and the model can always pick another name.
    """
    # Both ends, not just the source: a move whose DESTINATION is an invented
    # /Users/<guess> would silently create that tree and strand the files
    # somewhere the user will never look.
    for candidate in (source, destination):
        if (msg := _wrong_account_path(candidate)):
            return msg
    src = Path(source).expanduser()
    try:
        os.lstat(src)      # lstat: a broken symlink is a real thing to move
    except PermissionError:
        return _tcc_message(src, "moving")
    except FileNotFoundError:
        try:
            os.listdir(src.parent)
        except PermissionError:
            return _tcc_message(src, "moving")
        except OSError:
            pass
        return f"(no such path: {src})"
    except OSError as e:
        return f"(can't reach {src}: {e})"
    dst = Path(destination).expanduser()
    # A rename that only changes letter case (notes.txt -> Notes.txt) names the
    # same file on a case-insensitive volume, so it is not "already exists".
    if (not destination.endswith(("/", os.sep)) and dst.name != src.name
            and dst.name.lower() == src.name.lower() and dst.parent == src.parent
            and os.path.lexists(dst) and os.path.samefile(src, dst)):
        try:
            os.rename(src, dst)
            return f"moved {src} -> {dst}"
        except Exception as e:  # noqa: BLE001
            return f"(error moving {src} to {dst}: {e})"
    # "into this folder" vs "to this exact path". A trailing separator or an
    # existing directory means the former — the shape `mv` itself uses, and the
    # one a model reaching for "move X into Y/" naturally writes.
    if destination.endswith(("/", os.sep)) or dst.is_dir():
        dst = dst / src.name
    if os.path.lexists(dst):
        return (f"(refusing to overwrite {dst} — it already exists. "
                f"Move it somewhere else or pick a different name.)")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"moved {src} -> {dst}"
    except Exception as e:  # noqa: BLE001
        return f"(error moving {src} to {dst}: {e})"


