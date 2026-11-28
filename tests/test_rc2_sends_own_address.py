"""A send is judged on where it really goes, not only on the name typed.

The own-address guard used to look only at `to` as written. A contact name that
resolves to the user's own address (a "Me" card, or a fact that saved the
user's address under someone else's name) slipped past it and the message went
to the user. Now the resolved recipient is judged too, before the card and
inside the tool, with the same confirmed_self_send escape. Synthetic contacts,
a synthetic identity and a fake app bridge; nothing is sent.
"""
from __future__ import annotations

import pytest

import service.main  # noqa: F401 — registers every tool
from service.tools import action_tools, imessage_tools, registry

ME = "me.fixture@example.com"
FRIEND = "friend.fixture@example.com"
TEXT, EMAIL = registry.REGISTRY["send_message"], registry.REGISTRY["send_email"]


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(imessage_tools, "_name_handles", {
        "my card": [ME],                       # the user's own card
        "mom": [ME],                           # a contact wrongly saved with the user's address
        "friend": [FRIEND],
    })
    monkeypatch.setattr(imessage_tools, "_contacts", {ME: "My Card", FRIEND: "Friend"})
    monkeypatch.setattr(imessage_tools, "_lines", "")
    monkeypatch.setattr("service.memory.identity.user_emails", lambda: [ME])
    sent: list[tuple[str, dict]] = []

    async def request(op, payload, **kwargs):
        sent.append((op, payload))
        return {"ok": True, "status": "succeeded"}
    monkeypatch.setattr(action_tools, "app_request", request)
    return sent


@pytest.mark.parametrize("name", ["Mom", "My Card"])
def test_a_text_to_a_name_that_resolves_to_me_gets_no_card(world, name):
    args, problem = action_tools.prepare_send_recipient(TEXT, {"to": name, "text": "Hi"})
    assert problem is not None and problem.needs_recipient
    assert registry.not_sent_receipt(TEXT, problem)
    assert ME in problem and "OWN" in problem and name in problem.question
    assert world == []


async def test_the_text_tool_itself_refuses_a_name_that_resolves_to_me(world):
    refused = await action_tools.send_message(to="Mom", text="Hi")
    assert refused.needs_recipient and ME in refused and world == []
    # The same as typing the address.
    typed = await action_tools.send_message(to=ME, text="Hi")
    assert typed.needs_recipient and world == []


async def test_a_confirmed_self_send_still_goes(world):
    args, problem = action_tools.prepare_send_recipient(
        TEXT, {"to": "My Card", "text": "Hi", "confirmed_self_send": True})
    assert problem is None
    assert await action_tools.send_message(**args) == f"{action_tools.MESSAGE_HANDED_OVER_PREFIX}My Card ({ME}). " + action_tools.MESSAGE_HANDED_OVER
    assert world == [("send_message", {"to": ME, "text": "Hi"})]


async def test_someone_else_is_unaffected(world):
    args, problem = action_tools.prepare_send_recipient(TEXT, {"to": "Friend", "text": "Hi"})
    assert problem is None
    await action_tools.send_message(**args)
    assert world == [("send_message", {"to": FRIEND, "text": "Hi"})]


async def test_an_email_to_a_name_that_resolves_to_me_is_caught_before_and_inside(world):
    base = {"subject": "s", "body": "b"}
    _, problem = action_tools.prepare_send_recipient(EMAIL, {**base, "to": "Mom"})
    assert problem is not None and problem.needs_recipient and ME in problem
    refused = await action_tools.send_email("Mom", "s", "b")
    assert refused.needs_recipient and world == []
    assert await action_tools.send_email("Mom", "s", "b", confirmed_self_send=True) == f"Email sent to {ME}."


async def test_my_address_among_several_recipients_is_caught(world):
    _, problem = action_tools.prepare_send_recipient(
        EMAIL, {"to": f"Friend, {ME}", "subject": "s", "body": "b"})
    assert problem is not None and problem.needs_recipient
    assert "resolves to" not in problem      # typed literally, so worded as typed
    refused = await action_tools.send_email(f"{FRIEND}, Mom", "s", "b")
    assert refused.needs_recipient and world == []


def test_the_guard_judges_only_what_it_is_given():
    # No identity: nothing is the user's own address.
    assert action_tools._own_address_guard("Mom", "send_message") is None


# --- tools audit 3.2: forward, schedule and cc are guarded too; one de-garbled text ----------------

FORWARD, SCHEDULE = registry.REGISTRY["forward_email"], registry.REGISTRY["schedule_send"]


def test_a_forward_to_my_own_address_gets_no_card(world):
    _, problem = action_tools.prepare_send_recipient(FORWARD, {"message_id": "<m@x>", "to": "Mom"})
    assert problem is not None and ME in problem
    args, problem = action_tools.prepare_send_recipient(FORWARD, {"message_id": "<m@x>", "to": "Friend",
                                                                  "note": "See below\\nthanks"})
    assert problem is None and args["to"] == FRIEND and args["note"] == "See below\\nthanks"


async def test_the_forward_tool_itself_refuses_my_own_address(world):
    refused = await action_tools.forward_email("<m@x>", ME)
    assert ME in refused and world == []


def test_cc_to_my_own_address_is_caught_before_the_card(world):
    _, problem = action_tools.prepare_send_recipient(EMAIL, {"to": FRIEND, "cc": ME, "subject": "s", "body": "b"})
    assert problem is not None and ME in problem


async def test_the_email_tool_refuses_cc_to_my_own_address(world):
    refused = await action_tools.send_email(FRIEND, "s", "b", cc="Mom")
    assert ME in refused and world == []


def test_schedule_send_settles_time_and_recipient_before_the_card(world):
    past, problem = action_tools.prepare_send_recipient(
        SCHEDULE, {"channel": "email", "to": "Friend", "when": "2020-01-01T09:00:00-08:00", "body": "Hi"})
    assert problem is not None and "past" in problem
    _, problem = action_tools.prepare_send_recipient(
        SCHEDULE, {"channel": "email", "to": "Mom", "when": "2099-01-01T09:00:00-08:00", "body": "Hi"})
    assert problem is not None and ME in problem
    args, problem = action_tools.prepare_send_recipient(
        SCHEDULE, {"channel": "email", "to": "Friend", "when": "2099-01-01T09:00:00-08:00", "body": "Line one\\nLine two"})
    assert problem is None and args["to"] == FRIEND and args["body"] == "Line one\nLine two"
    _, problem = action_tools.prepare_send_recipient(
        SCHEDULE, {"channel": "email", "to": "Nobody Known", "when": "2099-01-01T09:00:00-08:00", "body": "Hi"})
    assert problem is not None                                  # unresolvable: asked before any card


def test_the_card_shows_exactly_the_text_the_send_carries(world):
    # A card is sent exactly as shown, so typed backslashes ("C:\\new") survive; the
    # preflight leaves the text raw and the preview shows that same raw text.
    args, problem = action_tools.prepare_send_recipient(
        EMAIL, {"to": FRIEND, "subject": "It\\'s on", "body": "Hi\\nSee you at 5"})
    assert problem is None and args["body"] == "Hi\\nSee you at 5" and args["subject"] == "It\\'s on"
    assert "Hi\\nSee you at 5" in action_tools.confirm_preview("send_email", args)
    text_args, _ = action_tools.prepare_send_recipient(TEXT, {"to": "Friend", "text": "On my way\\nsoon"})
    assert text_args["text"] == "On my way\\nsoon"
    assert "On my way\\nsoon" in action_tools.confirm_preview("send_message", text_args)


# --- tools audit 3.2: forward_email, schedule_send and cc get the same check ---

@pytest.fixture
def ask_mode(monkeypatch):
    from service.safety import policy
    from service.tools import self_send
    monkeypatch.setattr(policy, "_FULL_ACCESS", False)
    monkeypatch.setattr(policy, "_READ_ONLY", False)
    self_send.mark_turn("")
    yield policy
    self_send.mark_turn("")


@pytest.mark.parametrize("tool, args", [
    ("forward_email", {"message_id": "<m1@example.com>", "to": ME}),
    ("forward_email", {"message_id": "<m1@example.com>", "to": f"{FRIEND}, {ME}"}),
    ("schedule_send", {"channel": "email", "to": ME, "when": "tomorrow 9am", "body": "Hi"}),
    ("schedule_send", {"channel": "message", "to": ME, "when": "tomorrow 9am", "body": "Hi"}),
    ("send_email", {"to": FRIEND, "cc": ME, "subject": "Hi", "body": "Hi"}),
])
def test_every_outbound_send_to_my_own_address_is_refused_before_a_card(world, ask_mode, tool, args):
    decision = ask_mode.decide(registry.REGISTRY[tool].category, args, tool)
    assert decision.tier == ask_mode.Tier.DENY and "OWN" in decision.reason


@pytest.mark.parametrize("tool, args", [
    ("forward_email", {"message_id": "<m1@example.com>", "to": FRIEND}),
    ("schedule_send", {"channel": "email", "to": FRIEND, "when": "tomorrow 9am", "body": "Hi"}),
    ("send_email", {"to": FRIEND, "cc": "pat.fixture@example.com", "subject": "Hi", "body": "Hi"}),
])
def test_a_send_to_someone_else_still_reaches_its_card(world, ask_mode, tool, args):
    decision = ask_mode.decide(registry.REGISTRY[tool].category, args, tool)
    assert decision.tier == ask_mode.Tier.CONFIRM, decision.reason


def test_a_forward_the_person_aimed_at_themselves_reaches_its_card(world, ask_mode):
    from service.tools import self_send
    self_send.mark_turn("Forward this to my email")
    args = {"message_id": "<m1@example.com>", "to": ME}
    assert ask_mode.decide(registry.REGISTRY["forward_email"].category, args,
                           "forward_email").tier == ask_mode.Tier.CONFIRM


@pytest.mark.parametrize("tool, args", [
    ("forward_email", {"message_id": "<m1@example.com>", "to": "555-0100"}),
    ("send_email", {"to": f"{FRIEND}, 555 0100 22", "subject": "Hi", "body": "Hi"}),
    ("send_email", {"to": FRIEND, "cc": "+1 555 0100", "subject": "Hi", "body": "Hi"}),
    ("schedule_send", {"channel": "Email", "to": "555-0100", "when": "tomorrow 9am", "body": "Hi"}),
])
def test_a_phone_number_is_never_an_email_recipient(world, ask_mode, tool, args):
    decision = ask_mode.decide(registry.REGISTRY[tool].category, args, tool)
    assert decision.tier == ask_mode.Tier.DENY and "phone number" in decision.reason


# --- integration follow-ups: a confirmed self-send retry for forward and schedule; a named
# recipient is never re-addressed to "me" -------------------------------------------------

def test_forward_and_schedule_accept_a_confirmed_self_send_retry(world):
    from service.tools import self_send
    self_send.mark_turn("")
    try:
        _, problem = action_tools.prepare_send_recipient(
            registry.REGISTRY["forward_email"], {"message_id": "<m@x>", "to": ME})
        assert problem is not None and ME in problem
        _, problem = action_tools.prepare_send_recipient(
            registry.REGISTRY["forward_email"], {"message_id": "<m@x>", "to": ME, "confirmed_self_send": True})
        assert problem is None
        _, problem = action_tools.prepare_send_recipient(
            registry.REGISTRY["schedule_send"], {"channel": "email", "to": ME, "confirmed_self_send": True,
                                                 "when": "2099-01-01T09:00:00-08:00", "body": "Hi"})
        assert problem is None
        for name in ("forward_email", "schedule_send"):
            assert "confirmed_self_send" in registry.REGISTRY[name].parameters["properties"]
    finally:
        self_send.mark_turn("")


def test_a_recipient_the_person_named_is_never_rewritten_to_me(world):
    from service.tools import self_send
    self_send.mark_turn("Text me and Dana: hi Dana")
    try:
        assert action_tools._self_not_invented("Dana", "hi Dana") == "Dana"
        self_send.mark_turn("Text myself: Harbor test")
        assert action_tools._self_not_invented("Harbor test", "Harbor test") == "me"
    finally:
        self_send.mark_turn("")


def test_a_speech_save_to_a_folder_is_checked_where_the_file_really_lands(tmp_path):
    from service.safety import policy
    assert policy._speech_target(str(tmp_path)) == tmp_path / "Speech.aiff"
    assert policy._speech_target(str(tmp_path / "talk")) == tmp_path / "talk.aiff"
    (tmp_path / "Speech.aiff").write_bytes(b"x")
    assert "refusing to overwrite" in policy._speech_save_problem({"save_to": str(tmp_path)})
