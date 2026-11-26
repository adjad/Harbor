"""How accurate is the Brief? A read-only, counts-only check against Mail and Messages.

It reads the Brief's last saved output (``$HARBOR_HOME/proactive/state.json`` and
``decisions.json``, plain JSON reads, never the store class) and approximates what
the Brief serves (cycle.snapshot's filters). Then, for every shown row, it checks
ground truth straight from the user's own databases on this Mac, opened read-only
(``mode=ro``):

- Apple Mail's ``Envelope Index``: did the user send mail in the thread after its
  last incoming message (or is that message marked answered)? Is it read, deleted,
  or no longer in any inbox (archived or filed)?
- Messages' ``chat.db``: did the user send a text, or a tapback, after the chat's
  last incoming message? Is that message read?
- Dates: a to-do dated before today, an event or held slot already over.

It also estimates what the Brief missed (approximate): one-to-one chats and inbox
mail from the last few days whose newest message is an incoming direct question
("?") nobody answered, in conversations the Brief shows nothing for.

Privacy, by construction:
- Output is counts, fixed reason codes and per-item hashes only. A hash is an
  HMAC of the item's reference under a random salt made for this run and never
  stored, so a row can be followed within one report and never linked to
  anything outside it. No subject, name, address, body, number or id is printed
  or written. Message text is read in memory only, to look for "?".
- No network: every socket connect and name lookup is refused while it runs.
- No model, no service, no port: nothing from the running app is called, and
  nothing in ``service`` is imported.
- Nothing is changed: the Brief files are read as JSON, and the two databases are
  opened with ``mode=ro``.
- Without Full Disk Access the databases can't be opened; the check says so and
  stops (exit code 3) instead of reporting partial numbers.
"""
from __future__ import annotations

import contextlib
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

APPLE_EPOCH = 978307200                 # 2001-01-01 in Unix time (chat.db dates)
FN_WINDOW_DAYS = 3                      # the false-negative scan's look-back
TEXT_MATCH_S = 1.0                      # a Brief text's received_ts against chat.db's date
FYI_READ_S = 86400                      # cycle.FYI_READ_S
DONE_SHOWN_S = 86400                    # store._DONE_SHOWN_S
EXIT_NO_ACCESS = 3

# Fixed vocabularies: nothing outside these ever reaches the report.
VERDICTS = ("correct", "false_positive", "unverifiable", "unchecked")
REASONS = {
    # overdue_work: a dated to-do (not an event) whose day has passed. The Brief keeps late
    # work on purpose, and asks Keep/Delete about overdue reminders, so it is correct.
    "correct": ("open", "done_shown", "deadline_ahead", "task_after_reply", "overdue_work"),
    # read_fyi: read and over a day old (the Brief drops these only when it knows the read state);
    # read_fyi_recent: read, under a day old, kept by design (cycle.read_fyi_expired).
    # past_event: an event card, held slot or event to-do that is already over.
    "false_positive": ("replied", "read_fyi", "read_fyi_recent", "archived", "deleted", "past_event"),
    "unverifiable": ("not_found", "ambiguous", "no_reference", "source_unavailable"),
    "unchecked": ("no_ground_truth",),
}
KINDS = ("thread", "card", "todo")
SOURCES = ("mail", "messages", "calendar", "reminders", "notes", "other")
METHODS = ("email_reply", "text_reply", "read", "reminder", "calendar_event", "block_time", "add_to_day",
           "none", "other")
TODO_KINDS = ("reply", "read", "task", "event", "reminder", "other")
EVIDENCE = ("sent_in_thread", "answered_flag", "sent_references", "text_after", "tapback_after",
            "read", "no_inbox_copy", "deleted_copy", "group_chat")
_REPLY_METHODS = {"email_reply", "text_reply", "read"}
_REPLY_TITLE = re.compile(r"^\s*(?:reply|respond|answer|get back|text|email|message|write back|confirm"
                          r"|let\s+\S+(?:\s+\S+)?\s+know)\b", re.I)


class NoAccess(Exception):
    """A database exists but can't be opened: Full Disk Access is missing."""


# --- safety ----------------------------------------------------------------------------------


@contextlib.contextmanager
def no_network():
    """Every socket connect and name lookup fails while the check runs."""
    def refuse(*_args, **_kwargs):
        raise OSError("network is disabled during the Brief accuracy check")
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
             socket.getaddrinfo)
    socket.socket.connect = refuse                  # type: ignore[method-assign]
    socket.socket.connect_ex = refuse               # type: ignore[method-assign]
    socket.create_connection = refuse               # type: ignore[assignment]
    socket.getaddrinfo = refuse                     # type: ignore[assignment]
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
         socket.getaddrinfo) = saved


def open_readonly(path: Path) -> sqlite3.Connection:
    """The database opened read-only. Raises NoAccess when it can't be read."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.execute("select count(*) from sqlite_master").fetchone()
    except (sqlite3.Error, OSError) as exc:
        raise NoAccess(str(path.name)) from exc
    conn.execute("pragma query_only = 1")
    return conn


class Hasher:
    """Opaque per-run ids: HMAC-SHA256 under a salt that is never stored."""

    def __init__(self, salt: bytes | None = None):
        self._salt = salt or secrets.token_bytes(32)

    def __call__(self, *parts) -> str:
        msg = "\x1f".join(str(p) for p in parts).encode()
        return hmac.new(self._salt, msg, hashlib.sha256).hexdigest()[:16]


def _pick(value, allowed: tuple[str, ...]) -> str:
    return value if value in allowed else allowed[-1]


# --- paths -----------------------------------------------------------------------------------


@dataclass
class Paths:
    brief_dir: Path
    mail_index: Path | None
    chat_db: Path | None
    out_dir: Path

    @classmethod
    def default(cls) -> "Paths":
        moe = Path(os.environ["HARBOR_HOME"]).expanduser() if os.environ.get("HARBOR_HOME") else Path.home() / ".moe"
        home = Path.home()
        mail = None
        try:
            found = sorted(glob.glob(str(home / "Library/Mail/V*/MailData/Envelope Index")),
                           key=lambda p: int(re.search(r"/V(\d+)/", p).group(1)) if re.search(r"/V(\d+)/", p) else 0)
            mail = Path(found[-1]) if found else None
        except OSError:
            mail = None
        chat = home / "Library/Messages/chat.db"
        return cls(brief_dir=moe / "proactive", mail_index=mail, chat_db=chat, out_dir=moe / "diagnostics")


def access_problems(paths: Paths) -> list[str]:
    """The sources that exist but can't be read (Full Disk Access). Mail's folder
    itself is unreadable without it, so a hidden Envelope Index counts too."""
    problems = []
    mail_root = Path.home() / "Library/Mail"
    if paths.mail_index is None and mail_root.exists():
        try:
            os.listdir(mail_root)
        except OSError:
            problems.append("Mail")
    for name, path in (("Mail", paths.mail_index), ("Messages", paths.chat_db)):
        if path is None or name in problems:
            continue
        try:
            exists = path.exists()
        except OSError:
            exists = True
        if not exists and name == "Messages":
            try:
                os.listdir(path.parent)
            except FileNotFoundError:
                continue
            except OSError:
                problems.append(name)
            continue
        if exists:
            try:
                open_readonly(path).close()
            except NoAccess:
                problems.append(name)
    return problems


# --- the Brief, as served ----------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_brief(brief_dir: Path) -> tuple[dict, dict]:
    return _read_json(brief_dir / "state.json"), _read_json(brief_dir / "decisions.json")


def _num(value) -> float | None:
    return float(value) if type(value) in (int, float) and value == value else None


def _attention(thread: dict, now: float) -> str:
    """cycle.attention's kind, by the same rules."""
    if thread.get("needs_reply"):
        return "reply"
    due = _num((thread.get("deadline") or {}).get("due_ts"))
    if due is not None and due >= now:
        return "deadline"
    return "fyi"


# Fingerprints that are the item's id alone ("reminder:<id>", cycle.py), not "<ref>:<ask>".
_ID_ONLY_PREFIXES = ("calendar", "reminder", "block")


def _ref_of_fingerprint(fp) -> str:
    if not isinstance(fp, str) or ":" not in fp:
        return ""
    head, _, rest = fp.partition(":")
    if head in _ID_ONLY_PREFIXES and rest:
        return fp
    return fp.rpartition(":")[0]


def _decision_set(decisions: dict, key: str) -> set[str]:
    value = decisions.get(key)
    return set(value) if isinstance(value, dict) else set()


def shown_items(state: dict, decisions: dict, now: float) -> list[dict]:
    """The rows the Brief serves from this state, approximately (cycle.snapshot):
    threads (Needs attention), cards and the stored to-dos. Rule-built calendar and
    reminder to-dos are not in the state file and are not counted."""
    dismissed = _decision_set(decisions, "dismissed") | _decision_set(decisions, "fired")
    suppressed = _decision_set(decisions, "suppressed")
    done = {k: v for k, v in (decisions.get("done") or {}).items()
            if isinstance(v, dict) and now - float(_num(v.get("at")) or 0) < DONE_SHOWN_S}
    cleared = decisions.get("cleared") if isinstance(decisions.get("cleared"), dict) else {}
    cleared_ids = set(cleared)
    cleared_asks = {v.get("ask") for v in cleared.values() if isinstance(v, dict) and v.get("ask")}
    seen = dict(state.get("seen")) if isinstance(state.get("seen"), dict) else {}
    # A card or to-do whose item has no "seen" entry: its thread, when the state has one.
    for thread in state.get("threads") or []:
        if not isinstance(thread, dict):
            continue
        ref = str(thread.get("id") or "").removeprefix("thread:")
        if ref and ref not in seen:
            reply_ref = thread.get("reply_ref")
            seen[ref] = {"received_ts": thread.get("received_ts"),
                         "message_id": reply_ref.get("message_id") if isinstance(reply_ref, dict) else None}

    def gone(record: dict) -> bool:
        if record.get("id") in dismissed:
            return True
        prints = record.get("fingerprints") or []
        if prints and prints[0] in suppressed:
            return True
        return any(a in suppressed for a in record.get("asks") or [])

    out: list[dict] = []
    for thread in state.get("threads") or []:
        if not isinstance(thread, dict):
            continue
        attention = _attention(thread, now)
        if thread.get("replied") and attention != "deadline":
            continue
        received = _num(thread.get("received_ts"))
        if (attention == "fyi" and thread.get("unread") is False and received is not None
                and now - received > FYI_READ_S):
            continue
        if thread.get("id") in cleared_ids or (cleared_asks & set(thread.get("asks") or [])):
            continue      # approximate: Clear also checks the row still reads the same
        ref = str(thread.get("id") or "").removeprefix("thread:")
        reply_ref = thread.get("reply_ref")
        out.append({"kind": "thread", "ref": ref, "source": thread.get("source"), "attention": attention,
                    "received_ts": received, "seen": seen.get(ref),
                    "message_id": (reply_ref or {}).get("message_id") if isinstance(reply_ref, dict) else None,
                    "reply_work": attention == "reply"})
    for card in state.get("cards") or []:
        if not isinstance(card, dict) or card.get("todo_id") in done or gone(card):
            continue
        action = card.get("action") or {}
        method = action.get("method") or "none"
        slot_start = _num((action.get("slot") or {}).get("start"))
        if method == "block_time" and (slot_start or 0) <= now:
            continue
        src = (card.get("sources") or [{}])[0] or {}
        ref = str(src.get("ref") or "") or _ref_of_fingerprint((card.get("fingerprints") or [""])[0])
        env = action.get("envelope") or {}
        start = _num(env.get("start"))
        end = start + 60 * float(_num(env.get("duration_min")) or 0) if start is not None else None
        slot_end = _num((action.get("slot") or {}).get("end"))
        out.append({"kind": "card", "ref": ref, "source": src.get("kind"), "method": method,
                    "event_end": end if method == "calendar_event" else None,
                    "slot_end": slot_end, "seen": seen.get(ref),
                    "received_ts": _num((seen.get(ref) or {}).get("received_ts")),
                    "reply_work": method in _REPLY_METHODS or bool(_REPLY_TITLE.match(str(card.get("title") or "")))})
    for todo in state.get("todos") or []:
        if not isinstance(todo, dict):
            continue
        is_done = todo.get("id") in done or bool(todo.get("done"))
        if not is_done and any(a in suppressed for a in todo.get("asks") or []):
            continue
        if todo.get("id") in cleared_ids or (cleared_asks & set(todo.get("asks") or [])):
            continue
        ref = _ref_of_fingerprint((todo.get("fingerprints") or [""])[0])
        out.append({"kind": "todo", "ref": ref, "source": todo.get("source"), "todo_kind": todo.get("kind"),
                    "done": is_done, "due_ts": _num(todo.get("due_ts")), "seen": seen.get(ref),
                    "received_ts": _num((seen.get(ref) or {}).get("received_ts")),
                    "reply_work": todo.get("kind") == "reply"})
    # A done to-do the state no longer holds is still shown checked for a day.
    held = {t.get("id") for t in state.get("todos") or [] if isinstance(t, dict)}
    for todo_id, entry in done.items():
        if todo_id not in held and isinstance(entry.get("todo"), dict):
            out.append({"kind": "todo", "ref": "", "source": entry["todo"].get("source"),
                        "todo_kind": entry["todo"].get("kind"), "done": True, "due_ts": None,
                        "seen": None, "received_ts": None, "reply_work": False, "id_only": todo_id})
    return out


# --- ground truth: Mail -------------------------------------------------------------------------


def _mailbox_kind(url: str) -> str:
    leaf = unquote(str(url or "")).rstrip("/").rsplit("/", 1)[-1].casefold()
    path = unquote(str(url or "")).casefold()
    if leaf == "inbox":
        return "inbox"
    if "sent" in leaf:
        return "sent"
    if any(w in leaf for w in ("trash", "deleted", "bin")):
        return "trash"
    if any(w in leaf for w in ("junk", "spam")):
        return "junk"
    if "draft" in leaf:
        return "drafts"
    if "archive" in leaf or "all mail" in path:
        return "archive"
    return "folder"


def _norm_id(value) -> str:
    return str(value or "").strip().strip("<>").strip().casefold()


@dataclass
class MailFacts:
    replied: bool = False
    read: bool = False
    archived: bool = False
    deleted: bool = False
    evidence: list[str] = field(default_factory=list)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("select count(*) from sqlite_master where type = 'table' and name = ?",
                        (name,)).fetchone()[0] > 0


class MailTruth:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ids: dict[str, list[int]] | None = None
        # A message's mailboxes: its own, plus its labels. Gmail in Apple Mail keeps
        # one copy in All Mail and says Inbox or Sent with a label.
        self.boxes = ("(select ROWID as msg, mailbox as box from messages union "
                      "select message_id, mailbox_id from labels)") if _has_table(conn, "labels") \
            else "(select ROWID as msg, mailbox as box from messages)"
        self.server_replied = _has_table(conn, "server_messages")
        self.mine = self._my_addresses()

    def _my_addresses(self) -> set[str]:
        rows = self.conn.execute(
            f"select distinct a.address from messages m join {self.boxes} x on x.msg = m.ROWID "
            f"join mailboxes b on b.ROWID = x.box join addresses a on a.ROWID = m.sender "
            f"where lower(b.url) like '%sent%'").fetchall()
        return {str(r[0] or "").strip().casefold() for r in rows if r[0]}

    def _id_index(self) -> dict[str, list[int]]:
        if self._ids is None:
            self._ids = {}
            for header, conv in self.conn.execute(
                    "select g.message_id_header, m.conversation_id from messages m "
                    "join message_global_data g on g.ROWID = m.global_message_id"):
                key = _norm_id(header)
                if key:
                    self._ids.setdefault(key, []).append(int(conv or 0))
        return self._ids

    def conversations_for(self, message_id) -> set[int]:
        return {c for c in self._id_index().get(_norm_id(message_id), []) if c}

    def _logical(self, conv_ids: set[int]) -> list[dict]:
        """One entry per message (copies across mailboxes merged), oldest first."""
        marks = ",".join("?" * len(conv_ids))
        rows = self.conn.execute(
            f"select m.global_message_id, m.message_id, m.date_received, m.date_sent, m.read, m.deleted, "
            f"m.flags, b.url, a.address, m.ROWID from messages m join {self.boxes} x on x.msg = m.ROWID "
            f"join mailboxes b on b.ROWID = x.box "
            f"left join addresses a on a.ROWID = m.sender where m.conversation_id in ({marks})",
            tuple(conv_ids)).fetchall()
        by: dict[int, dict] = {}
        for gid, mid, received, sent, read, deleted, flags, url, address, rowid in rows:
            entry = by.setdefault(int(gid or 0) or -len(by) - 1, {
                "mids": set(), "rowids": set(), "kinds": set(), "read": False, "deleted_all": True,
                "answered": False, "outgoing": False, "ts": 0.0})
            kind = _mailbox_kind(url)
            entry["mids"].add(mid)
            entry["rowids"].add(rowid)
            entry["kinds"].add(kind)
            entry["read"] |= bool(read) or bool((flags or 0) & 1)
            entry["deleted_all"] &= bool(deleted) or kind == "trash"
            entry["answered"] |= bool((flags or 0) & 4)
            if kind == "sent" or str(address or "").strip().casefold() in self.mine:
                entry["outgoing"] = True
            stamp = (sent if entry["outgoing"] and sent else received) or sent or 0
            entry["ts"] = max(entry["ts"], float(stamp))
        return sorted(by.values(), key=lambda e: e["ts"])

    def facts(self, message_id) -> MailFacts | None:
        convs = self.conversations_for(message_id)
        if not convs:
            return None
        messages = self._logical(convs)
        incoming = [m for m in messages if not m["outgoing"] and m["kinds"] != {"drafts"}]
        if not incoming:
            return None
        last = incoming[-1]
        facts = MailFacts()
        if any(m["outgoing"] and "drafts" not in m["kinds"] and m["ts"] > last["ts"] for m in messages):
            facts.replied = True
            facts.evidence.append("sent_in_thread")
        if not last["answered"] and self.server_replied and last["rowids"]:
            marks = ",".join("?" * len(last["rowids"]))
            last["answered"] = bool(self.conn.execute(
                f"select count(*) from server_messages where replied = 1 and message in ({marks})",
                tuple(last["rowids"])).fetchone()[0])
        if last["answered"]:
            facts.replied = True
            facts.evidence.append("answered_flag")
        if not facts.replied and last["mids"]:
            marks = ",".join("?" * len(last["mids"]))
            hit = self.conn.execute(
                f"select count(*) from message_references r join {self.boxes} x on x.msg = r.message "
                f"join mailboxes b on b.ROWID = x.box where r.reference in ({marks}) "
                f"and lower(b.url) like '%sent%'", tuple(last["mids"])).fetchone()[0]
            if hit:
                facts.replied = True
                facts.evidence.append("sent_references")
        facts.read = last["read"]
        if facts.read:
            facts.evidence.append("read")
        if last["deleted_all"] or last["kinds"] <= {"trash", "junk"}:
            facts.deleted = True
            facts.evidence.append("deleted_copy")
        elif "inbox" not in last["kinds"]:
            facts.archived = True
            facts.evidence.append("no_inbox_copy")
        return facts

    def open_questions(self, now: float, days: int) -> dict[int, str]:
        """Approximate misses: {conversation id: opaque key} for inbox mail from a
        person in the last `days`, addressed to the user, newest in its thread,
        asking a direct question ("?" in the preview or subject), not answered."""
        since = now - days * 86400
        rows = self.conn.execute(
            "select distinct m.conversation_id, m.ROWID, a.address, m.list_id_hash, m.automated_conversation, "
            f"m.unsubscribe_type, s.subject, su.summary from messages m join {self.boxes} x on x.msg = m.ROWID "
            "join mailboxes b on b.ROWID = x.box left join addresses a on a.ROWID = m.sender "
            "left join subjects s on s.ROWID = m.subject left join summaries su on su.ROWID = m.summary "
            "where m.date_received >= ? and m.deleted = 0 and lower(b.url) like '%inbox'", (since,)).fetchall()
        out: dict[int, str] = {}
        for conv, rowid, address, list_id, automated, unsubscribe, subject, summary in rows:
            address = str(address or "").strip().casefold()
            if not conv or not address or address in self.mine or list_id or automated or unsubscribe:
                continue
            if re.search(r"(no-?reply|do-?not-?reply|notification|mailer-daemon|bounce)", address):
                continue
            words = (str(summary or "") + " " + str(subject or ""))
            if "?" not in words:
                continue
            # Addressed to the user (To), when their addresses are known from Sent.
            to_me = self.conn.execute(
                "select count(*) from recipients r join addresses a on a.ROWID = r.address "
                "where r.message = ? and r.type = 0 and lower(a.address) in (%s)" % ",".join("?" * len(self.mine)),
                (rowid, *sorted(self.mine))).fetchone()[0] if self.mine else 1
            if not to_me:
                continue
            messages = self._logical({int(conv)})
            if not messages or messages[-1]["outgoing"]:
                continue                       # the user wrote last
            if messages[-1]["answered"]:
                continue
            out[int(conv)] = f"mailconv:{int(conv)}"
        return out


# --- ground truth: Messages ---------------------------------------------------------------------


@dataclass
class TextFacts:
    replied: bool = False
    read: bool = False
    group: bool = False
    chat: int = 0
    evidence: list[str] = field(default_factory=list)


def _apple_ns(ts: float) -> int:
    return int((ts - APPLE_EPOCH) * 1e9)


def _unix(apple) -> float:
    value = float(apple or 0)
    return value / 1e9 + APPLE_EPOCH if value > 1e11 else value + APPLE_EPOCH


def _body_text(text, body) -> str:
    """A message's words: ``text``, or the string inside ``attributedBody`` (a typedstream
    archive; newer Messages leaves ``text`` empty). Best effort, in memory only."""
    if text:
        return str(text)
    if not isinstance(body, (bytes, bytearray)) or b"NSString" not in body:
        return ""
    rest = bytes(body).split(b"NSString", 1)[1]
    start = rest.find(b"\x2b")
    if start < 0 or start + 2 > len(rest):
        return ""
    size, at = rest[start + 1], start + 2
    if size == 0x81:
        size, at = int.from_bytes(rest[at:at + 2], "little"), at + 2
    elif size == 0x82:
        size, at = int.from_bytes(rest[at:at + 4], "little"), at + 4
    return rest[at:at + size].decode("utf-8", "ignore")


_PLAIN = "(m.associated_message_type is null or m.associated_message_type = 0) and coalesce(m.item_type, 0) = 0"


class TextTruth:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def chat_for(self, received_ts: float) -> tuple[int | None, str]:
        """The chat whose incoming message has this timestamp, or (None, reason)."""
        lo, hi = _apple_ns(received_ts - TEXT_MATCH_S), _apple_ns(received_ts + TEXT_MATCH_S)
        chats = {r[0] for r in self.conn.execute(
            "select j.chat_id from message m join chat_message_join j on j.message_id = m.ROWID "
            "where m.is_from_me = 0 and m.date between ? and ?", (lo, hi))}
        if not chats:
            # Older chat.db rows store seconds, not nanoseconds.
            lo_s, hi_s = int(received_ts - TEXT_MATCH_S - APPLE_EPOCH), int(received_ts + TEXT_MATCH_S - APPLE_EPOCH)
            chats = {r[0] for r in self.conn.execute(
                "select j.chat_id from message m join chat_message_join j on j.message_id = m.ROWID "
                "where m.is_from_me = 0 and m.date between ? and ?", (lo_s, hi_s))}
        if not chats:
            return None, "not_found"
        if len(chats) > 1:
            return None, "ambiguous"
        return int(next(iter(chats))), ""

    def facts(self, chat: int) -> TextFacts:
        q = ("select max(m.date) from message m join chat_message_join j on j.message_id = m.ROWID "
             "where j.chat_id = ? and m.is_from_me = ? and ")
        last_in = self.conn.execute(q + _PLAIN, (chat, 0)).fetchone()[0]
        last_me = self.conn.execute(q + _PLAIN, (chat, 1)).fetchone()[0]
        last_tap = self.conn.execute(q + "m.associated_message_type between 2000 and 2006", (chat, 1)).fetchone()[0]
        style = self.conn.execute("select style from chat where ROWID = ?", (chat,)).fetchone()
        facts = TextFacts(chat=chat, group=bool(style and style[0] == 43))
        if facts.group:
            facts.evidence.append("group_chat")
        if last_in is None:
            return facts
        if last_me is not None and _unix(last_me) > _unix(last_in):
            facts.replied = True
            facts.evidence.append("text_after")
        if last_tap is not None and _unix(last_tap) > _unix(last_in):
            facts.replied = True
            facts.evidence.append("tapback_after")
        read = self.conn.execute(
            "select m.is_read from message m join chat_message_join j on j.message_id = m.ROWID "
            "where j.chat_id = ? and m.is_from_me = 0 and m.date = ? and " + _PLAIN, (chat, last_in)).fetchone()
        facts.read = bool(read and read[0])
        if facts.read:
            facts.evidence.append("read")
        return facts

    def open_questions(self, now: float, days: int) -> dict[int, str]:
        """Approximate misses: {chat: opaque key} for one-to-one chats with a person
        whose newest message (no tapbacks) arrived in the last `days`, asks
        something ("?"), and has no text or tapback from the user after it."""
        since = _apple_ns(now - days * 86400)
        rows = self.conn.execute(
            "select j.chat_id, max(m.date) from message m join chat_message_join j on j.message_id = m.ROWID "
            "join chat c on c.ROWID = j.chat_id where c.style = 45 and m.date >= ? and " + _PLAIN +
            " group by j.chat_id", (since,)).fetchall()
        out: dict[int, str] = {}
        for chat, newest in rows:
            last = self.conn.execute(
                "select m.is_from_me, m.text, h.id, m.attributedBody from message m join chat_message_join j on j.message_id = m.ROWID "
                "left join handle h on h.ROWID = m.handle_id where j.chat_id = ? and m.date = ? and " + _PLAIN,
                (chat, newest)).fetchone()
            if not last or last[0]:
                continue
            handle = str(last[2] or "")
            digits = re.sub(r"\D", "", handle)
            if "@" not in handle and len(digits) < 10:
                continue                      # short codes and services
            if "?" not in _body_text(last[1], last[3]):
                continue
            tap = self.conn.execute(
                "select max(m.date) from message m join chat_message_join j on j.message_id = m.ROWID "
                "where j.chat_id = ? and m.is_from_me = 1", (chat,)).fetchone()[0]
            if tap is not None and _unix(tap) > _unix(newest):
                continue
            out[int(chat)] = f"chat:{int(chat)}"
        return out


# --- classification -----------------------------------------------------------------------------


def _local_midnight(now: float) -> float:
    day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp()


def classify(item: dict, *, now: float, mail: MailTruth | None, texts: TextTruth | None) -> dict:
    """{verdict, reason, evidence, link} for one shown row. `link` is the source
    conversation key, used only to tell covered conversations from missed ones."""
    result = {"verdict": "correct", "reason": "open", "evidence": [], "link": None}

    def verdict(v: str, reason: str, evidence=()):
        result.update(verdict=v, reason=reason)
        result["evidence"] = [e for e in evidence if e in EVIDENCE]
        return result

    if item.get("done"):
        return verdict("correct", "done_shown")
    if item["kind"] == "todo" and item.get("due_ts") is not None and item["due_ts"] < _local_midnight(now):
        if item.get("todo_kind") == "event":
            return verdict("false_positive", "past_event")
        return verdict("correct", "overdue_work")
    for end in (item.get("event_end"), item.get("slot_end")):
        if end is not None and end < now:
            return verdict("false_positive", "past_event")
    source = item.get("source")
    if source not in ("mail", "messages"):
        return verdict("unchecked", "no_ground_truth")
    read_work = item.get("attention") == "fyi" or item.get("method") == "read" or item.get("todo_kind") == "read"
    if source == "mail":
        if mail is None:
            return verdict("unverifiable", "source_unavailable")
        mid = item.get("message_id") or (item.get("seen") or {}).get("message_id")
        if not mid:
            return verdict("unverifiable", "no_reference")
        facts = mail.facts(mid)
        if facts is None:
            return verdict("unverifiable", "not_found")
        convs = mail.conversations_for(mid)
        result["link"] = [f"mailconv:{c}" for c in sorted(convs)]
        if facts.deleted:
            return verdict("false_positive", "deleted", facts.evidence)
        if facts.archived:
            return verdict("false_positive", "archived", facts.evidence)
        replied, read, evidence = facts.replied, facts.read, facts.evidence
    else:
        if texts is None:
            return verdict("unverifiable", "source_unavailable")
        received = item.get("received_ts")
        if received is None:
            return verdict("unverifiable", "no_reference")
        chat, why = texts.chat_for(received)
        if chat is None:
            return verdict("unverifiable", why)
        result["link"] = [f"chat:{chat}"]
        facts_t = texts.facts(chat)
        replied, read, evidence = facts_t.replied, facts_t.read, facts_t.evidence
    if replied:
        if item.get("attention") == "deadline":
            return verdict("correct", "deadline_ahead", evidence)
        if item["kind"] == "thread" or item.get("reply_work") or read_work:
            return verdict("false_positive", "replied", evidence)
        return verdict("correct", "task_after_reply", evidence)
    if read and read_work:
        received = item.get("received_ts")
        recent = received is not None and now - received <= FYI_READ_S
        return verdict("false_positive", "read_fyi_recent" if recent else "read_fyi", evidence)
    return verdict("correct", "open", evidence)


# --- the run ------------------------------------------------------------------------------------


def _empty_counts() -> dict:
    return {kind: {v: {r: 0 for r in REASONS[v]} for v in VERDICTS} for kind in KINDS}


def run(paths: Paths, *, now: float | None = None, window_days: int = FN_WINDOW_DAYS,
        hasher: Hasher | None = None) -> dict:
    """The report: counts, reason codes and per-item hashes. Raises NoAccess."""
    now = time.time() if now is None else now
    hasher = hasher or Hasher()
    problems = access_problems(paths)
    if problems:
        raise NoAccess(", ".join(problems))
    state, decisions = load_brief(paths.brief_dir)
    mail_conn = open_readonly(paths.mail_index) if paths.mail_index and paths.mail_index.exists() else None
    chat_conn = open_readonly(paths.chat_db) if paths.chat_db and paths.chat_db.exists() else None
    try:
        mail = MailTruth(mail_conn) if mail_conn else None
        texts = TextTruth(chat_conn) if chat_conn else None
        items = shown_items(state, decisions, now)
        counts = _empty_counts()
        rows, covered = [], set()
        for item in items:
            outcome = classify(item, now=now, mail=mail, texts=texts)
            counts[item["kind"]][outcome["verdict"]][outcome["reason"]] += 1
            covered.update(outcome["link"] or [])
            rows.append({"item": hasher(item["kind"], item.get("ref") or item.get("id_only") or id(item)),
                         "kind": item["kind"], "source": _pick(item.get("source"), SOURCES),
                         "method": _pick(item.get("method"), METHODS) if item["kind"] == "card" else None,
                         "todo_kind": _pick(item.get("todo_kind"), TODO_KINDS) if item["kind"] == "todo" else None,
                         "verdict": outcome["verdict"], "reason": outcome["reason"],
                         "evidence": outcome["evidence"]})
        missed = {"mail": [], "messages": []}
        if mail:
            missed["mail"] = [hasher("miss", k) for k in mail.open_questions(now, window_days).values()
                              if k not in covered]
        if texts:
            missed["messages"] = [hasher("miss", k) for k in texts.open_questions(now, window_days).values()
                                  if k not in covered]
    finally:
        for conn in (mail_conn, chat_conn):
            if conn is not None:
                conn.close()
    totals = {v: sum(sum(counts[k][v].values()) for k in KINDS) for v in VERDICTS}
    judged = totals["correct"] + totals["false_positive"]
    as_of = _num(state.get("as_of"))
    return {
        "version": 1,
        "generated_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "brief": {"present": bool(state), "age_minutes": round((now - as_of) / 60, 1) if as_of else None,
                  "shown": len(items)},
        "sources": {"mail": mail_conn is not None, "messages": chat_conn is not None},
        "counts": counts,
        "totals": totals,
        "precision": round(totals["correct"] / judged, 3) if judged else None,
        "false_negatives": {"approximate": True, "window_days": window_days,
                            "heuristic": "newest message in a one-to-one chat or inbox thread is an incoming "
                                         "direct question ('?'), unanswered, with no Brief row for it",
                            "mail": len(missed["mail"]), "messages": len(missed["messages"]),
                            "items": missed["mail"] + missed["messages"]},
        "items": rows,
        "notes": ["Hashes use a per-run random salt that is not stored: they match only within this report.",
                  "Rule-built calendar and reminder to-dos are not in the Brief's state file and are not counted.",
                  "Mail read only through Gmail (not in Apple Mail) shows as unverifiable/not_found."],
    }


def render_table(report: dict) -> str:
    lines = []
    brief = report["brief"]
    lines.append(f"Brief accuracy check  {report['generated_at']}")
    lines.append(f"Brief state: {'present' if brief['present'] else 'missing'}, "
                 f"age {brief['age_minutes']} min, {brief['shown']} rows shown; "
                 f"sources: mail={'yes' if report['sources']['mail'] else 'no'}, "
                 f"messages={'yes' if report['sources']['messages'] else 'no'}")
    lines.append("")
    lines.append(f"{'verdict':<15} {'reason':<18} {'thread':>7} {'card':>6} {'todo':>6} {'total':>6}")
    lines.append("-" * 62)
    for v in VERDICTS:
        for r in REASONS[v]:
            row = [report["counts"][k][v][r] for k in KINDS]
            if sum(row):
                lines.append(f"{v:<15} {r:<18} {row[0]:>7} {row[1]:>6} {row[2]:>6} {sum(row):>6}")
    lines.append("-" * 62)
    t = report["totals"]
    lines.append(f"correct {t['correct']}  false_positive {t['false_positive']}  "
                 f"unverifiable {t['unverifiable']}  unchecked {t['unchecked']}  precision {report['precision']}")
    fn = report["false_negatives"]
    lines.append(f"missed (approximate, last {fn['window_days']} days): mail {fn['mail']}  messages {fn['messages']}")
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = out_dir / f"brief-accuracy-{time.strftime('%Y%m%d-%H%M%S')}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Counts-only, read-only check of the Brief against Mail and Messages.")
    ap.add_argument("--out-dir", type=Path, help="where the JSON goes (default ~/.moe/diagnostics)")
    ap.add_argument("--no-write", action="store_true", help="print the table only")
    ap.add_argument("--window-days", type=int, default=FN_WINDOW_DAYS, help="look-back for missed questions")
    args = ap.parse_args(argv)
    paths = Paths.default()
    if args.out_dir:
        paths.out_dir = args.out_dir
    with no_network():
        try:
            report = run(paths, window_days=max(1, args.window_days))
        except NoAccess as exc:
            print(f"Full Disk Access is missing (can't read: {exc}). Grant it to the app running this "
                  "check in System Settings > Privacy & Security > Full Disk Access, then run it again.",
                  file=sys.stderr)
            return EXIT_NO_ACCESS
    print(render_table(report))
    if not args.no_write:
        try:
            written = write_report(report, paths.out_dir)
        except OSError as exc:
            print(f"\nThe JSON could not be written ({type(exc).__name__}); the table above stands.",
                  file=sys.stderr)
            return 1
        home = str(Path.home())
        shown = "~" + str(written)[len(home):] if str(written).startswith(home + os.sep) else str(written)
        print(f"\nJSON: {shown}")
    return 0
