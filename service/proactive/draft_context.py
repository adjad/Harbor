"""What a reply draft is written from.

Triage reads a short, capped excerpt of each thread. A draft needs more: the
newest message nearly whole, what was said before it (the user's own replies
included), and whether the days it names look open on the calendar.

IMPORTANT rules:
- The longer material is built only when the drafting model runs on this Mac
  (on_device). Otherwise the caller keeps the short, strictly redacted item text.
- A credential's value (a code, a key, a password with its value, a card or ID
  number) is masked; a message that still holds one after masking is left out
  whole. Ordinary words like "confirm" or "security" change nothing. Links,
  addresses and phone numbers are replaced, so there is nothing of the kind to
  copy into a draft.
- Only people in the conversation: an earlier message joins a thread when Mail
  puts it in the same conversation, or when the same person is on it. Two people
  who used the same subject never meet in one draft.
- The calendar gives busy times only, never an event's title: the draft goes to
  someone else.
- Mail profile facts are never read here.
- Nothing here sends, writes or loads a model. Every read fails quietly to less
  material.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parseaddr
from zoneinfo import ZoneInfo

from service.proactive import guard

NEWEST_CHARS = 3000
EARLIER_CHARS = 800
MAX_EARLIER = 4
MAX_TEXT_EARLIER = 12
TOTAL_CHARS = 6500
MIN_CHARS = 1200
OUTPUT_TOKENS = 600          # room held for the draft itself
SPARE_TOKENS = 500           # headers, fences and the closing lines
MAX_DAYS = 3
DAYS_AHEAD = 21
INDEX_DAYS = 60
INDEX_TTL_S = 300.0
INDEX_TIMEOUT_S = 15.0
BODY_TIMEOUT_S = 6.0
THREAD_TIMEOUT_S = 20.0

_index_cache: tuple[float, list[dict]] | None = None


def on_device() -> bool:
    """The drafting model is Harbor's own engine on this Mac: the one check the
    item text is redacted by (sources.reading_on_device), so a draft's longer
    material and its short text always agree. Fails closed."""
    from service.proactive import sources
    return sources.reading_on_device()


def writing_style() -> str:
    """The measured writing card, only when switched on and the model is on-device
    (service/memory/prompt_blocks.style_block decides both)."""
    try:
        from service.config.endpoints import role_target
        from service.memory.prompt_blocks import style_block
        return style_block(role_target("fast").endpoint)
    except Exception:  # noqa: BLE001 — the rules alone still draft
        return ""


def budget_chars(system: str) -> int:
    """Characters of material that fit the drafting model's window."""
    try:
        from service.config.endpoints import role_target
        window = int(role_target("fast").context_window or 8000)
    except Exception:  # noqa: BLE001
        window = 8000
    tokens = window - OUTPUT_TOKENS - SPARE_TOKENS - len(system) // 3
    return max(MIN_CHARS, min(TOTAL_CHARS, tokens * 3))


# --- one message's own words ---------------------------------------------------

_URL = re.compile(r"(?:https?://|www\.)[^\s<>()]+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|edu|gov|ly|me|ai|app|"
                  r"dev|xyz)\b(?:/[^\s<>()]*)?", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w/])\+?\(?\d(?:[\s().-]{0,2}\d){9,14}(?![\w/])")
_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}\b")
# A code or password followed, on its line or the next, by something with a digit in
# it. A bare "code" needs "is", ":" or "=" ("course code CS101" is no secret).
_CODE_VALUE = re.compile(
    r"\b(?:(?:pass(?:word|code|phrase)?|pin|otp|token|(?:login|log[ -]?in|sign[ -]?in|access|security|verification|"
    r"recovery|backup|auth\w*)[ -]?(?:code|key))\b[ \t]*(?:is|:|=|-)?|code\b[ \t]*(?:is|:|=))"
    r"[ \t]*\n?[ \t]*[\"'“]?(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{4,}", re.I)
# A password stated outright, digits or not.
_PASSWORD_STATED = re.compile(r"\bpass(?:word|code|phrase)\b[ \t]*(?:is|:|=)[ \t]*\n?[ \t]*(?!\(omitted\))\S{4,}",
                              re.I)
# redact_secret_values' stand-ins. Rounded, so a draft that copies one isn't refused for "[".
_MASKED = re.compile(r"\[(?:secret |code |number |link details )?omitted\]")
_FORWARDED = re.compile(r"(?im)^[ \t]*(?:-{2,} *forwarded message *-{2,}|begin forwarded message:)")
_QUOTE_LINE = re.compile(r"^[ \t]*(?:>|&gt;|\|)")
_ATTRIBUTION = re.compile(r"(?i)^[ \t]*(?:on|am|le|el)\b.*\b(?:wrote|schrieb|a écrit|escribió)[ \t]*:?[ \t]*$")
SHORT_OWN_WORDS = 40


def own_words(body: str) -> str:
    """The message without its quoted history. An answer written between quoted
    lines keeps every line that isn't quoted; a forward keeps what it forwards."""
    from service.assistant.daily_workspace import _QUOTED_HISTORY
    body = str(body or "")
    if _FORWARDED.search(body):
        return body.strip()
    head = _QUOTED_HISTORY.split(body)[0].strip()
    if len(head) >= SHORT_OWN_WORDS or head == body.strip():
        return head
    lines = body.splitlines()
    first = next((i for i, line in enumerate(lines) if _QUOTE_LINE.match(line)), None)
    if first is None:
        return head                      # a forward or a header block: nothing of theirs below it
    kept = [line for line in lines if not _QUOTE_LINE.match(line) and not _ATTRIBUTION.match(line)]
    return "\n".join(kept).strip()


def holds_secret(text: str) -> bool:
    """A credential's value, a card number or an ID number. Stricter about what
    counts as a value than guard.source_sensitive, so "the code works" stays."""
    from service.proactive.triage import _RAW_SECRET
    text = guard._without_benign_code(text or "")
    return bool(guard._SSN.search(text) or guard.has_card_number(text) or _RAW_SECRET.search(text)
                or _CODE_VALUE.search(text) or _PASSWORD_STATED.search(text))


def safe_text(text: str) -> str:
    """The words a draft may be written from. Secret values are masked the way the
    rest of the Brief's on-device reading masks them
    (imessage_tools.redact_secret_values); "" for a message that still holds one
    after that."""
    from service.tools.imessage_tools import redact_secret_values
    text = _MASKED.sub("(omitted)", redact_secret_values(str(text or "").strip()))
    if not text or holds_secret(text):
        return ""
    text = _EMAIL.sub("(email address)", text)
    text = _URL.sub("(link)", text)
    text = _PHONE.sub("(phone number)", text)
    text = _OPAQUE.sub("(id)", text)
    text = text.replace("<<<", "‹‹‹").replace(">>>", "›››")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text))


def _sentence_head(text: str, limit: int) -> str:
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?][\"'”)]?(?=\s)|\n", cut)]
    if ends and ends[-1] >= limit // 2:
        return cut[:ends[-1]].rstrip()
    return cut.rsplit(" ", 1)[0].rstrip()


def cut_at_sentence(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a sentence end. The last paragraph is
    kept when it fits in a quarter of the room: the question is often there."""
    if len(text) <= limit:
        return text
    last = text.rstrip().rpartition("\n\n")[2].strip()
    if last and len(last) <= limit // 4:
        return f"{_sentence_head(text, limit - len(last) - 5)} …\n\n{last}"
    return _sentence_head(text, limit - 2) + " …"


# --- the mail thread -----------------------------------------------------------


def _message_id(value) -> str:
    return str(value or "").strip().strip("<>").strip().casefold()


def _address(row: dict) -> str:
    return str(row.get("sender_address") or parseaddr(str(row.get("sender") or ""))[1]).strip().casefold()


def _recipients(row: dict) -> set[str]:
    to = row.get("to")
    values = [str(v) for v in to] if isinstance(to, list) else [str(to or "")]
    return {a.casefold() for _n, a in getaddresses(values) if "@" in a}


def my_addresses() -> set[str]:
    try:
        from service.memory.identity import user_emails
        return {e.casefold() for e in user_emails()}
    except Exception:  # noqa: BLE001
        return set()


def _norm_subject(subject) -> str:
    return re.sub(r"^\s*((re|fwd?|aw|sv)\s*:\s*)+", "", str(subject or ""), flags=re.I).strip().casefold()


def cached_thread(row: dict, rows: list[dict], *, mine: set[str] | None = None) -> list[dict]:
    """Earlier cached messages of `row`'s thread (same account and subject), oldest
    first: the other person's, and the user's own to that person (marked "mine")."""
    mine = my_addresses() if mine is None else mine
    key, account, ts = _norm_subject(row.get("subject")), row.get("account"), row.get("ts") or 0
    if not key:
        return []
    them = _address(row)
    seen, out = {_message_id(row.get("message_id"))}, []
    for other in sorted(rows, key=lambda r: r.get("ts") or 0, reverse=True):
        mid = _message_id(other.get("message_id"))
        if (mid and mid in seen) or other.get("account") != account or (other.get("ts") or 0) >= ts \
                or _norm_subject(other.get("subject")) != key:
            continue
        sender = _address(other)
        from_user = bool(sender) and sender in mine
        if from_user:
            if not them or them not in _recipients(other):
                continue
        elif sender != them:
            continue
        seen.add(mid)
        out.append({**other, "mine": from_user})
    return list(reversed(out))


async def _index() -> list[dict]:
    """Mail's index for the last weeks, read at most every INDEX_TTL_S."""
    global _index_cache
    now = time.monotonic()
    if _index_cache is not None and now - _index_cache[0] < INDEX_TTL_S:
        return _index_cache[1]
    from service.mail_memory import bridge
    try:
        rows, _capped = await bridge.read_index(INDEX_DAYS, timeout=INDEX_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — the app is away or Mail can't be read
        rows = []
    _index_cache = (now, rows)
    return rows


async def _body(rowid: int):
    from service.mail_memory import bridge
    from service.mail_memory.extract import parse_raw
    try:
        raw, _why = await bridge.read_body(rowid, timeout=BODY_TIMEOUT_S)
        return None if raw is None else parse_raw(raw, 0.0)
    except Exception:  # noqa: BLE001 — that message is simply not available
        return None


async def _from_mail(row: dict, earlier: list[dict], found: dict) -> None:
    """Fill `found` from Mail itself: the newest message's body when the cache has
    none, and Mail's own conversation, the user's Sent replies included."""
    index = await _index()
    want = _message_id(row.get("message_id"))
    target = next((r for r in index if want and want in (_message_id(r.get("rfc_message_id")),
                                                         _message_id(r.get("message_id")))), None)
    if target is None:
        return
    if not str(row.get("body") or "").strip():
        mail = await _body(target["rowid"])
        if mail is not None:
            found["body"] = mail.body
    conversation = target.get("conversation_id")
    if not conversation:
        return
    before = float(row.get("ts") or target.get("ts") or 0)
    them = _address(row)
    have = {_message_id(m.get("message_id")) for m in earlier if str(m.get("body") or "").strip()}
    others = [r for r in index if r.get("conversation_id") == conversation and r["rowid"] != target["rowid"]
              and float(r.get("ts") or 0) < before and _message_id(r.get("rfc_message_id")) not in have]
    seen = set()
    for other in sorted(others, key=lambda r: r.get("ts") or 0, reverse=True):
        mid = _message_id(other.get("rfc_message_id")) or f"row:{other['rowid']}"
        if mid in seen:
            continue
        seen.add(mid)
        mail = await _body(other["rowid"])
        if mail is None or not mail.body.strip():
            continue
        # Mail can group by subject: only messages this correspondent is on.
        if not them or not (them == _address({"sender": mail.sender}) or them in _recipients({"to": mail.to})):
            continue
        found["earlier"].append({"sender": mail.sender, "to": mail.to, "subject": mail.subject,
                                 "ts": other.get("ts"), "body": mail.body, "account": row.get("account"),
                                 "message_id": mid, "mine": other.get("box") == "sent"})
        if len(found["earlier"]) >= MAX_EARLIER:
            break


async def mail_thread(row: dict, rows: list[dict], *, local: bool) -> tuple[dict, list[dict]]:
    """(the message to answer, earlier messages oldest first). With `local`, bodies
    the cache lacks and the rest of Mail's conversation are read from Mail; a
    slow read keeps what arrived in time."""
    earlier = cached_thread(row, rows)
    found: dict = {"body": None, "earlier": []}
    if local:
        try:
            await asyncio.wait_for(_from_mail(row, earlier, found), THREAD_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — what was read before it stands
            pass
    if found["body"]:
        row = {**row, "body": found["body"]}
    merged = {}
    for message in earlier + found["earlier"]:
        key = _message_id(message.get("message_id")) or f"ts:{message.get('ts')}"
        if key not in merged or not str(merged[key].get("body") or "").strip():
            merged[key] = message
    ordered = sorted(merged.values(), key=lambda m: m.get("ts") or 0)
    return row, ordered[-MAX_EARLIER:]


# --- the calendar --------------------------------------------------------------

_DAY_WORDS = re.compile(
    r"\b(?:today|tonight|tomorrow|(?:next\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?|"
    r"(?<![\d/])\d{1,2}/\d{1,2}(?![\d/]))\b", re.I)


def named_days(text: str, arrival_ts: float, now: float, tz: str) -> list[date]:
    """The coming days a message names, as read when it arrived (at most MAX_DAYS)."""
    zone = ZoneInfo(tz)
    today = datetime.fromtimestamp(now, zone).date()
    days: list[date] = []
    for match in _DAY_WORDS.finditer(guard.normalize_slang(text or "")):
        due, _label, _clock = guard.resolve_due(match.group(0), arrival_ts, tz)
        if due is None:
            continue
        day = datetime.fromtimestamp(due, zone).date()
        if today <= day <= today + timedelta(days=DAYS_AHEAD) and day not in days:
            days.append(day)
        if len(days) >= MAX_DAYS:
            break
    return days


def _calendar_events(start: float, end: float) -> list[dict] | None:
    """Events overlapping the day, or None when the calendar hasn't been read."""
    from service.assistant import sync_status
    if sync_status.source_status("calendar").get("state") != "ready":
        return None
    from service.assistant.store import assistant_store
    hidden = assistant_store.hidden_commitment_ids(time.time())
    return [r for r in assistant_store.calendar_events_overlapping(start, end) if r.get("id") not in hidden]


def free_busy(days: list[date], tz: str, events_fn=None) -> str:
    """One line per day: its busy times, or that nothing is on the calendar."""
    zone = ZoneInfo(tz)

    def clock(ts: float) -> str:
        return datetime.fromtimestamp(ts, zone).strftime("%-I:%M %p")

    lines = []
    for day in days:
        start = datetime.combine(day, datetime.min.time(), zone).timestamp()
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).timestamp()
        try:
            rows = (events_fn or _calendar_events)(start, end)
        except Exception:  # noqa: BLE001 — an unread day is not a free day
            rows = None
        if rows is None:
            continue
        all_day, spans = False, []
        for row in rows:
            if row.get("all_day"):
                all_day = True
                continue
            begin = max(start, float(row["when_ts"]))
            finish = row.get("end_ts")
            finish = min(end, float(finish)) if finish else begin
            spans.append([begin, max(begin, finish)])
        merged: list[list[float]] = []
        for begin, finish in sorted(spans):
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], finish)
            else:
                merged.append([begin, finish])
        parts = [f"busy {clock(b)} to {clock(f)}" if f > b else f"something at {clock(b)}" for b, f in merged[:6]]
        if all_day:
            parts.insert(0, "has an all-day entry")
        name = day.strftime("%A, %b %-d")
        lines.append(f"{name}: {'; '.join(parts) if parts else 'nothing on the calendar'}")
    return "\n".join(lines)


# --- assembling it -------------------------------------------------------------


def _stamp(ts, tz: str) -> str:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, ZoneInfo(tz)).strftime("%a %b %-d %-I:%M %p")


def _speaker(message: dict) -> str:
    if message.get("mine"):
        return "You (the user)"
    name, address = parseaddr(str(message.get("sender") or ""))
    return guard.clean(name or (address.partition("@")[0] if address else message.get("sender")), 60) or "Them"


def assemble(newest: str, earlier: list[str], calendar: str, budget: int) -> tuple[str, str]:
    """(fenced material, the text a draft is held to). The newest message comes
    first for the room, then the calendar; the oldest history is dropped first."""
    newest = cut_at_sentence(newest, min(NEWEST_CHARS, max(MIN_CHARS - 200, budget - len(calendar))))
    room = budget - len(newest) - len(calendar)
    kept: list[str] = []
    for block in reversed(earlier):
        if len(block) + 2 > room:
            break
        kept.insert(0, block)
        room -= len(block) + 2
    parts = []
    if kept:
        parts.append("EARLIER IN THIS CONVERSATION (oldest first):\n" + "\n\n".join(kept))
    parts.append("THE MESSAGE TO ANSWER:\n" + newest)
    material = "<<<ITEM\n" + "\n\n".join(parts) + "\nITEM>>>"
    if calendar:
        material += "\n<<<CALENDAR (the user's busy times; no event details)\n" + calendar + "\nCALENDAR>>>"
    return material, "\n".join(kept + [newest, calendar])


async def _mail_context(raw: dict, *, now: float, tz: str, budget: int) -> tuple[str, str] | None:
    from service.assistant import email_reply_api as mail
    ref = mail._parse_ref(raw.get("reply_ref"))
    if ref is None:
        return None
    row = mail._find_row(ref)
    if row is None:
        return None
    row, earlier = await mail_thread(row, mail._mail_rows(), local=True)
    words = safe_text(own_words(row.get("body")))
    if not words:
        return None
    subject = safe_text(row.get("subject")) or "(withheld)"
    newest = f"From {_speaker(row)} · {_stamp(row.get('ts'), tz)}\nSubject: {subject}\n\n{words}"
    blocks = []
    for message in earlier:
        said = safe_text(own_words(message.get("body")))
        if said:
            blocks.append(f"From {_speaker(message)} · {_stamp(message.get('ts'), tz)}:\n"
                          + cut_at_sentence(said, EARLIER_CHARS))
    calendar = free_busy(named_days(f"{subject}\n{words}", float(row.get("ts") or now), now, tz), tz)
    return assemble(newest, blocks, calendar, budget)


def _text_rows(conversation: str) -> list[tuple[float, str]]:
    from service.proactive import sources
    return [(r[0], r[2]) for r in sources.text_records() if r[1] == conversation]


def _text_context(raw: dict, *, now: float, tz: str, budget: int) -> tuple[str, str] | None:
    from service.proactive import sources
    conversation = (raw.get("ref") or {}).get("conversation")
    if not conversation:
        return None
    rows = [(ts, text) for ts, text in _text_rows(conversation) if now - sources.TEXT_WINDOW_S <= ts <= now + 300]
    if not rows:
        return None
    last_mine = max((i for i, (_t, line) in enumerate(rows) if line.partition(":")[0].strip() == "Me"), default=-1)

    def shown(ts: float, line: str) -> str:
        sender, _, body = line.partition(":")
        sender = "You (the user)" if sender.strip() == "Me" else guard.clean(sender, 60)
        body = safe_text(body)
        return f"{sender} · {_stamp(ts, tz)}: {body}" if body else ""

    pending = rows[last_mine + 1:] or rows[-1:]
    before = rows[:len(rows) - len(pending)][-MAX_TEXT_EARLIER:]
    newest = "\n".join(filter(None, (shown(*r) for r in pending)))
    if not newest:
        return None
    if len(newest) > NEWEST_CHARS:
        newest = "… " + newest[-NEWEST_CHARS:].partition("\n")[2]      # the latest lines are the ones to answer
    blocks = [b for b in (shown(*r) for r in before) if b]
    calendar = free_busy(named_days("\n".join(line.partition(":")[2] for _t, line in pending), pending[-1][0], now, tz),
                         tz)
    return assemble(newest, blocks, calendar, budget)


async def build(item: dict, *, now: float, tz: str, budget: int) -> tuple[str, str] | None:
    """(material, grounding text) for a judged item, or None to use its short text."""
    raw = item.get("item") or {}
    if raw.get("secret") or raw.get("critical"):
        return None
    try:
        if raw.get("source") == "mail":
            return await _mail_context(raw, now=now, tz=tz, budget=budget)
        if raw.get("source") == "messages":
            return _text_context(raw, now=now, tz=tz, budget=budget)
    except Exception:  # noqa: BLE001 — the short text still drafts
        import traceback
        traceback.print_exc()
    return None
