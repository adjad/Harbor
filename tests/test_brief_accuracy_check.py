"""The Brief accuracy check: invented Brief state, invented Mail and Messages databases.

Every value here is made up. The databases are tiny SQLite files with only the
columns the check reads, built in tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import stat
from pathlib import Path

import pytest

from service.proactive import accuracy_check as ac

NOW = 1_900_000_000.0          # a fixed "now"
DAY = 86400.0
ME = "me@example.org"
PAL = "pal@example.com"
SECRET_WORDS = ["Lunch on Friday?", "Pat Example", PAL, ME, "+15550000001", "Can you come tonight?",
                "<invented-1@example.com>", "invented-1@example.com", "Field trip form"]


# --- invented Mail ----------------------------------------------------------------------------

MAIL_SCHEMA = """
create table mailboxes (ROWID integer primary key, url text);
create table addresses (ROWID integer primary key, address text, comment text);
create table subjects (ROWID integer primary key, subject text);
create table summaries (ROWID integer primary key, summary text);
create table message_global_data (ROWID integer primary key, message_id_header text);
create table messages (ROWID integer primary key, message_id integer, global_message_id integer,
  sender integer, subject integer, summary integer, date_sent real, date_received real, mailbox integer,
  flags integer default 0, read integer default 0, deleted integer default 0, conversation_id integer,
  list_id_hash integer, automated_conversation integer default 0, unsubscribe_type integer default 0);
create table recipients (ROWID integer primary key, message integer, address integer, type integer, position integer);
create table message_references (ROWID integer primary key, message integer, reference integer, is_originator integer);
"""


class Mail:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(MAIL_SCHEMA)
        for rowid, url in ((1, "imap://acct/INBOX"), (2, "imap://acct/Sent%20Messages"),
                           (3, "imap://acct/%5BGmail%5D/All%20Mail"), (4, "imap://acct/Trash")):
            self.db.execute("insert into mailboxes values (?, ?)", (rowid, url))
        self.addr = {}
        self.n = 0

    def address(self, value: str) -> int:
        if value not in self.addr:
            self.addr[value] = len(self.addr) + 1
            self.db.execute("insert into addresses values (?, ?, '')", (self.addr[value], value))
        return self.addr[value]

    def add(self, *, header: str, conv: int, sender: str, ts: float, mailboxes=(1,), read=0, flags=0,
            deleted=0, subject="Hello", summary="", to=ME, list_id=None) -> int:
        self.n += 1
        gid = self.n
        self.db.execute("insert into message_global_data values (?, ?)", (gid, header))
        self.db.execute("insert into subjects values (?, ?)", (gid, subject))
        self.db.execute("insert into summaries values (?, ?)", (gid, summary))
        for box in mailboxes:
            cur = self.db.execute(
                "insert into messages (message_id, global_message_id, sender, subject, summary, date_sent, "
                "date_received, mailbox, flags, read, deleted, conversation_id, list_id_hash) "
                "values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1000 + gid, gid, self.address(sender), gid, gid, ts, ts, box, flags | (1 if read else 0), read,
                 deleted, conv, list_id))
            self.db.execute("insert into recipients (message, address, type, position) values (?, ?, 0, 0)",
                            (cur.lastrowid, self.address(to)))
        self.db.commit()
        return 1000 + gid

    def close(self):
        self.db.close()


# --- invented Messages --------------------------------------------------------------------------

CHAT_SCHEMA = """
create table chat (ROWID integer primary key, guid text, style integer, chat_identifier text);
create table handle (ROWID integer primary key, id text);
create table message (ROWID integer primary key, guid text, text text, attributedBody blob,
  handle_id integer, date integer,
  is_from_me integer, is_read integer default 0, associated_message_type integer default 0,
  item_type integer default 0);
create table chat_message_join (chat_id integer, message_id integer, message_date integer);
"""


class Chats:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.executescript(CHAT_SCHEMA)
        self.n = 0

    def chat(self, rowid: int, handle: str, style: int = 45):
        self.db.execute("insert into chat values (?, ?, ?, ?)", (rowid, f"iMessage;-;{handle}", style, handle))
        self.db.execute("insert into handle values (?, ?)", (rowid, handle))

    def say(self, chat: int, ts: float, text: str | None, *, me: bool = False, read: int = 0, tapback: int = 0,
            body: bytes | None = None):
        self.n += 1
        apple = int((ts - ac.APPLE_EPOCH) * 1e9)
        self.db.execute("insert into message values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (self.n, f"g{self.n}", text, body, 0 if me else chat, apple, int(me), read, tapback))
        self.db.execute("insert into chat_message_join values (?, ?, ?)", (chat, self.n, apple))
        self.db.commit()

    def close(self):
        self.db.close()


# --- invented Brief ----------------------------------------------------------------------------


def thread(ref, source, *, received, needs_reply=True, message_id=None, deadline=None):
    t = {"id": f"thread:{ref}", "source": source, "account": "me", "who": "Pat Example",
         "title": "Lunch on Friday?", "summary": "", "user_wrote": False, "needs_reply": needs_reply,
         "asked": needs_reply, "replied": False, "deadline": deadline, "received_ts": received, "asks": []}
    t["reply_ref"] = {"message_id": message_id} if message_id else f"messages:Pat Example"
    return t


@pytest.fixture()
def world(tmp_path):
    brief = tmp_path / "moe" / "proactive"
    brief.mkdir(parents=True)
    mail = Mail(tmp_path / "Envelope Index")
    chats = Chats(tmp_path / "chat.db")
    paths = ac.Paths(brief_dir=brief, mail_index=tmp_path / "Envelope Index", chat_db=tmp_path / "chat.db",
                     out_dir=tmp_path / "moe" / "diagnostics")
    yield {"brief": brief, "mail": mail, "chats": chats, "paths": paths}
    mail.close()
    chats.close()


def save(brief: Path, state: dict, decisions: dict | None = None):
    (brief / "state.json").write_text(json.dumps({"as_of": NOW - 600, **state}))
    (brief / "decisions.json").write_text(json.dumps(decisions or {}))


def by_reason(report):
    out = {}
    for row in report["items"]:
        out.setdefault((row["kind"], row["verdict"], row["reason"]), 0)
        out[(row["kind"], row["verdict"], row["reason"])] += 1
    return out


# --- tests ----------------------------------------------------------------------------------------


def test_mail_threads_classified_against_envelope_index(world):
    m = world["mail"]
    m.add(header="<open@x>", conv=1, sender=PAL, ts=NOW - 3600)
    m.add(header="<answered@x>", conv=2, sender=PAL, ts=NOW - 7200)
    m.add(header="<mine@x>", conv=2, sender=ME, ts=NOW - 3000, mailboxes=(2,))
    m.add(header="<flagged@x>", conv=3, sender=PAL, ts=NOW - 7200, flags=4)
    m.add(header="<filed@x>", conv=4, sender=PAL, ts=NOW - 7200, mailboxes=(3,))
    m.add(header="<binned@x>", conv=5, sender=PAL, ts=NOW - 7200, mailboxes=(4,))
    m.add(header="<fyi@x>", conv=6, sender=PAL, ts=NOW - 7200, read=1)
    save(world["brief"], {"threads": [
        thread("mail:a", "mail", received=NOW - 3600, message_id="open@x"),
        thread("mail:b", "mail", received=NOW - 7200, message_id="ANSWERED@x"),
        thread("mail:c", "mail", received=NOW - 7200, message_id="<flagged@x>"),
        thread("mail:d", "mail", received=NOW - 7200, message_id="filed@x"),
        thread("mail:e", "mail", received=NOW - 7200, message_id="binned@x"),
        thread("mail:f", "mail", received=NOW - 7200, message_id="fyi@x", needs_reply=False),
        thread("mail:g", "mail", received=NOW - 7200, message_id="nowhere@x"),
    ]})
    report = ac.run(world["paths"], now=NOW)
    got = by_reason(report)
    assert got[("thread", "correct", "open")] == 1
    assert got[("thread", "false_positive", "replied")] == 2
    assert got[("thread", "false_positive", "archived")] == 1
    assert got[("thread", "false_positive", "deleted")] == 1
    assert got[("thread", "false_positive", "read_fyi_recent")] == 1
    assert got[("thread", "unverifiable", "not_found")] == 1
    evidence = sorted(e for row in report["items"] if row["reason"] == "replied" for e in row["evidence"])
    assert evidence == ["answered_flag", "sent_in_thread"]
    assert report["totals"]["false_positive"] == 5
    assert report["precision"] == round(1 / 6, 3)


def test_a_newer_incoming_message_reopens_a_replied_thread(world):
    m = world["mail"]
    m.add(header="<q1@x>", conv=1, sender=PAL, ts=NOW - 9000)
    m.add(header="<r1@x>", conv=1, sender=ME, ts=NOW - 8000, mailboxes=(2,))
    m.add(header="<q2@x>", conv=1, sender=PAL, ts=NOW - 7000)
    save(world["brief"], {"threads": [thread("mail:a", "mail", received=NOW - 9000, message_id="q1@x")]})
    assert by_reason(ac.run(world["paths"], now=NOW)) == {("thread", "correct", "open"): 1}


def test_texts_use_chat_db_with_tapbacks(world):
    c = world["chats"]
    c.chat(1, "+15550000001")
    c.chat(2, "+15550000002")
    c.chat(3, "+15550000003")
    c.say(1, NOW - 5000, "Can you come tonight?")
    c.say(1, NOW - 4000, "", me=True, tapback=2000)          # the user's own heart
    c.say(2, NOW - 5100, "Can you come tonight?")
    c.say(3, NOW - 5200, "See you there", read=1)
    c.chat(4, "+15550000004")
    c.say(4, NOW - 3 * DAY, "Old news", read=1)
    save(world["brief"], {"threads": [
        thread("messages:a", "messages", received=NOW - 5000),
        thread("messages:b", "messages", received=NOW - 5100),
        thread("messages:c", "messages", received=NOW - 5200, needs_reply=False),
        thread("messages:d", "messages", received=NOW - 3 * DAY, needs_reply=False),
    ]})
    report = ac.run(world["paths"], now=NOW)
    assert by_reason(report) == {("thread", "false_positive", "replied"): 1, ("thread", "correct", "open"): 1,
                                 ("thread", "false_positive", "read_fyi_recent"): 1,
                                 ("thread", "false_positive", "read_fyi"): 1}
    replied = next(r for r in report["items"] if r["reason"] == "replied")
    assert replied["evidence"] == ["tapback_after"]


def test_view_filters_dates_cards_and_todos(world):
    m = world["mail"]
    m.add(header="<form@x>", conv=1, sender=PAL, ts=NOW - 3600)
    m.add(header="<form-re@x>", conv=1, sender=ME, ts=NOW - 1800, mailboxes=(2,))
    seen = {"mail:form": {"source": "mail", "message_id": "<form@x>", "received_ts": NOW - 3600}}
    cards = [
        {"id": "card:reply", "title": "Reply to Pat", "todo_id": "todo:1", "fingerprints": ["mail:form:h1"],
         "sources": [{"kind": "mail", "ref": "mail:form"}], "action": {"method": "email_reply"}},
        {"id": "card:task", "title": "Field trip form", "todo_id": "todo:2", "fingerprints": ["mail:form:h2"],
         "sources": [{"kind": "mail", "ref": "mail:form"}], "action": {"method": "reminder"}},
        {"id": "card:gone", "title": "x", "fingerprints": ["mail:form:h3"],
         "sources": [{"kind": "mail", "ref": "mail:form"}], "action": {"method": "email_reply"}},
        {"id": "card:slot", "title": "x", "fingerprints": ["block:1"], "sources": [{"kind": "calendar", "ref": "c"}],
         "action": {"method": "block_time", "slot": {"start": NOW - 60, "end": NOW + 600}}},
        {"id": "card:event", "title": "x", "fingerprints": ["calendar:1"], "sources": [{"kind": "calendar", "ref": "c"}],
         "action": {"method": "calendar_event", "envelope": {"start": NOW - 7200, "duration_min": 30}}},
    ]
    todos = [
        {"id": "todo:old", "title": "x", "source": "reminders", "kind": "task", "done": False,
         "due_ts": NOW - 3 * DAY, "fingerprints": ["reminder:1"]},
        {"id": "todo:done", "title": "x", "source": "mail", "kind": "reply", "done": False,
         "due_ts": NOW + DAY, "fingerprints": ["mail:form:h4"]},
        {"id": "todo:later", "title": "x", "source": "notes", "kind": "task", "done": False,
         "due_ts": NOW + DAY, "fingerprints": ["note:1"]},
    ]
    save(world["brief"], {"seen": seen, "cards": cards, "todos": todos},
         {"dismissed": {"card:gone": NOW - 10}, "done": {"todo:done": {"at": NOW - 60}}})
    got = by_reason(ac.run(world["paths"], now=NOW))
    assert got == {("card", "false_positive", "replied"): 1, ("card", "correct", "task_after_reply"): 1,
                   ("card", "false_positive", "past_event"): 1, ("todo", "false_positive", "past_date"): 1,
                   ("todo", "correct", "done_shown"): 1, ("todo", "unchecked", "no_ground_truth"): 1}


def test_false_negatives_are_open_direct_questions_without_a_row(world):
    c, m = world["chats"], world["mail"]
    c.chat(1, "+15550000001")
    c.chat(2, "+15550000002")
    c.chat(3, "+15550000003", style=43)                      # a group: not counted
    c.chat(4, "12345")                                        # a short code: not counted
    c.chat(5, "+15550000005")
    c.chat(6, "+15550000006")
    c.say(6, NOW - 650, None, body=b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
          b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x0bAre you in?\x86")
    c.say(1, NOW - 600, "Can you come tonight?")             # shown below
    c.say(2, NOW - 700, "Free for lunch?")                    # missed
    c.say(3, NOW - 800, "Who's in?")
    c.say(4, NOW - 900, "Reply STOP?")
    c.say(5, NOW - 1000, "Done?")
    c.say(5, NOW - 900, "yes", me=True)                       # answered
    m.add(header="<ask@x>", conv=7, sender=PAL, ts=NOW - 1200, summary="Could you send the form?")
    m.add(header="<news@x>", conv=8, sender=PAL, ts=NOW - 1200, summary="Big sale?", list_id=99)
    m.add(header="<old@x>", conv=9, sender=PAL, ts=NOW - 5 * DAY, summary="Still on?")
    save(world["brief"], {"threads": [thread("messages:a", "messages", received=NOW - 600)]})
    report = ac.run(world["paths"], now=NOW)
    fn = report["false_negatives"]
    assert (fn["messages"], fn["mail"], fn["approximate"]) == (2, 1, True)
    assert len(set(fn["items"])) == 3


def test_attributed_body_text_is_read_in_memory():
    blob = b"\x84\x84\x08NSString\x01\x94\x84\x01+\x05Free?\x86"
    assert ac._body_text(None, blob) == "Free?"
    assert ac._body_text("plain", blob) == "plain"
    assert ac._body_text(None, b"garbage") == ""


def test_report_never_holds_content_and_hashes_are_per_run(world, tmp_path):
    m, c = world["mail"], world["chats"]
    m.add(header="<invented-1@example.com>", conv=1, sender=PAL, ts=NOW - 600, subject="Lunch on Friday?",
          summary="Can you come tonight?")
    c.chat(1, "+15550000001")
    c.say(1, NOW - 500, "Can you come tonight?")
    save(world["brief"], {"threads": [
        thread("mail:a", "mail", received=NOW - 600, message_id="<invented-1@example.com>"),
        thread("messages:a", "messages", received=NOW - 500)],
        "seen": {"mail:a": {"subject": "Lunch on Friday?", "sender": PAL, "message_id": "<invented-1@example.com>"}}})
    one = ac.run(world["paths"], now=NOW, hasher=ac.Hasher(b"a" * 32))
    two = ac.run(world["paths"], now=NOW, hasher=ac.Hasher(b"b" * 32))
    text = json.dumps(one) + ac.render_table(one)
    for word in SECRET_WORDS + ["mail:a", "messages:a", "thread:"]:
        assert word not in text
    assert {r["item"] for r in one["items"]}.isdisjoint({r["item"] for r in two["items"]})
    assert all(len(r["item"]) == 16 for r in one["items"])
    path = ac.write_report(one, world["paths"].out_dir)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["totals"] == one["totals"]


def test_sources_are_left_untouched(world):
    world["mail"].add(header="<a@x>", conv=1, sender=PAL, ts=NOW - 600)
    world["chats"].chat(1, "+15550000001")
    world["chats"].say(1, NOW - 500, "hi?")
    save(world["brief"], {"threads": [thread("mail:a", "mail", received=NOW - 600, message_id="a@x")]})
    files = [world["paths"].mail_index, world["paths"].chat_db, world["brief"] / "state.json",
             world["brief"] / "decisions.json"]
    before = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    ac.run(world["paths"], now=NOW)
    assert {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files} == before
    assert sorted(p.name for p in world["brief"].iterdir()) == ["decisions.json", "state.json"]


def test_readonly_connection_refuses_writes(world):
    conn = ac.open_readonly(world["paths"].chat_db)
    with pytest.raises(sqlite3.Error):
        conn.execute("insert into handle values (99, 'x')")
    conn.close()


def test_network_is_refused_while_running():
    with ac.no_network():
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 8775), timeout=0.1)
        with pytest.raises(OSError):
            socket.getaddrinfo("example.com", 443)
    assert socket.create_connection is not None and socket.getaddrinfo.__name__ != "refuse"


def test_missing_full_disk_access_stops_with_exit_3(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ac, "access_problems", lambda paths: ["Messages"])
    monkeypatch.setattr(ac.Paths, "default", classmethod(lambda cls: ac.Paths(
        brief_dir=tmp_path, mail_index=None, chat_db=None, out_dir=tmp_path / "out")))
    assert ac.main(["--no-write"]) == ac.EXIT_NO_ACCESS
    assert "Full Disk Access" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_unreadable_database_counts_as_no_access(tmp_path):
    bad = tmp_path / "chat.db"
    bad.write_bytes(b"not a database at all, invented bytes" * 10)
    paths = ac.Paths(brief_dir=tmp_path, mail_index=None, chat_db=bad, out_dir=tmp_path)
    assert ac.access_problems(paths) == ["Messages"]


def test_module_imports_nothing_from_the_service():
    source = Path(ac.__file__).read_text()
    assert "from service" not in source and "import service" not in source
    for banned in ("urllib.request", "requests", "httpx", "8765", "omlx"):
        assert banned not in source.split('"""', 2)[2]
