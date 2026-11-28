"""Whether the person's OWN message named themselves as the recipient this turn.

The agent loop calls `mark_turn(text)` once per turn with the person's own
words (never model text, never a tool result). Send tools read `named_self()`.

Only the recipient slot of a send counts: a clause that starts with a send
verb and is aimed at "me"/"myself" ("email myself a test", "send me a text",
"forward this to my email"). "Email Dana and tell her to call me" names Dana:
the "me" there belongs to a later clause that is not a send. Quoted text and
the body after a colon ("Text Dana: email me later") never count.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

_named_self: ContextVar[bool] = ContextVar("harbor_user_named_self", default=False)

_VERB = r"(?:e-?mail|mail|text|i?message|send|forward|dm|ping|cc|bcc)"
_ME = r"(?:me|myself)"
# "my own address", "my email", "my phone number": the person's own handle.
_MINE = (r"(?:my\s+own\s+(?:e-?mail(?:\s+address)?|address|inbox|account|number|phone(?:\s+number)?|cell)"
         r"|my\s+(?:e-?mail(?:\s+address)?|inbox|(?:phone|cell)(?:\s+number)?|number))")
# Polite or connective words a clause may start with before its verb.
_LEAD = (r"^(?:(?:please|pls|hey|hi|ok(?:ay)?|harbor|now|just|also|then|and|so|quickly|"
         r"can|could|would|will|you|go\s+ahead\s+and|i\s+(?:want|need)\s+you\s+to)\b\s*)*")
# "email me …", "text myself …", "send me a text …", "text my own number".
_DIRECT = re.compile(_LEAD + _VERB + r"\s+(?:(?:it|this|that)\s+)?(?:" + _ME + "|" + _MINE + r")\b", re.I)
# Words that, between the send verb and "to me", start a clause of their own:
# a body ("that …", "saying …", "tell …"), a relative clause ("the file he
# sent to me", "the invoice Bob sent to my email"), or another subject. The
# "to me" then belongs to that clause, not to the send, so it never counts.
_NOT_THE_SEND = (
    r"that|saying|says|tell|ask|who|whom|whose|which|because|where|when|while|"
    r"i|he|she|they|we|you|it's|someone|somebody|everyone|everybody|people|"
    r"sent|sends|gave|gives|given|wrote|writes|written|forwarded|emailed|e-mailed|mailed|"
    r"texted|messaged|got|received|showed|shown|handed|passed|shared|addressed")
# "send the notes to me", "forward this to my email": a send clause aimed at
# me, with nothing in between that starts a clause of its own.
_TO_ME = re.compile(
    _LEAD + _VERB + r"\b(?:(?!\b(?:" + _NOT_THE_SEND + r")\b).)*?"
    r"\bto\s+(?:" + _ME + "|" + _MINE + r")\b", re.I)
# After "to me", another recipient in the same clause ("… to my email to
# Sarah", "… to me to sarah@example.com"): the send is aimed there instead.
# A capitalised word, an address, a number or a pronoun for someone else.
_LATER_RECIPIENT = re.compile(
    r"\bto\s+(?!(?:me|myself|my)\b)(?:[A-Z][\w'-]*|[\w.+-]+@[\w-]|\+?\d|"
    r"(?i:him|her|them|us)\b)")
# Clause boundaries: sentence ends, commas, and "and"/"then"/"but".
_CLAUSES = re.compile(r"[.;!?\n,]+|\s+(?:and|then|but|also)\s+", re.I)


def user_names_self(text: str) -> bool:
    """True when the person's own message names themselves as a send's recipient."""
    # A colon after the instruction starts the message body: "Text myself: hi".
    # "Hey Harbor: email me …": a colon not after a send is not a body.
    head = _request_head(text)
    return any(_DIRECT.match(c.strip()) or _to_me(c.strip())
               for c in _CLAUSES.split(head) if c.strip())


def _to_me(clause: str) -> bool:
    """A send clause aimed at me, with no other recipient after the "to me"."""
    m = _TO_ME.match(clause)
    return bool(m) and not _LATER_RECIPIENT.search(clause, m.end())


_turn_text: ContextVar[str] = ContextVar("harbor_user_turn_text", default="")


def mark_turn(text: str) -> None:
    _named_self.set(bool(user_names_self(text or "")))
    _turn_text.set(text or "")


def turn_text() -> str:
    """The person's own message this turn (never model text)."""
    return _turn_text.get()


def typed_this_turn(handle: str) -> bool:
    """The person's own message this turn contains this address or number."""
    said, want = _turn_text.get().casefold(), (handle or "").strip().casefold()
    if not want:
        return False
    digits = re.sub(r"\D", "", want)
    return want in said or ("@" not in want and len(digits) >= 7 and digits in re.sub(r"\D", "", said))


def _request_head(text: str) -> str:
    """The instruction part of the person's message: quoted spans masked and
    the body after a send's colon left out, as user_names_self reads it."""
    from service.utterance_shape import mask_quoted

    plain = " ".join(mask_quoted(text or "").split())
    head = plain.split(":", 1)[0]
    return head if re.search(rf"\b{_VERB}\b", head, re.I) else plain


def named_in_request(name: str) -> bool:
    """The person's own instruction this turn names `name` as words of its own
    ("Text me and Dana: hi" names Dana). Text after the colon ("Text myself:
    Harbor test") and quoted text are the message, not a recipient, so they do
    not count. A send must never re-address a recipient this is true for."""
    want = " ".join((name or "").split())
    if not want:
        return False
    return re.search(rf"(?<!\w){re.escape(want)}(?!\w)", _request_head(_turn_text.get()),
                     re.I) is not None


def named_self() -> bool:
    return _named_self.get()
