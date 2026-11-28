"""rc3 tools-rest chunk 2: authoring check, read_file/list_dir bounds and TCC
messages, find_place category matching, text_to_speech argv. No real `say`, no
network, no user data."""
import subprocess

import pytest

from service.tools import tool_authoring as ta


@pytest.mark.parametrize("task", [
    "count words in the given text",
    "convert text to title case",
    "slugify some text",
    "compare two texts and list the differences",
    "strip new lines from pasted texts",
])
def test_plain_text_utilities_are_not_redirected(task):
    redirect, _ = ta._capability_check("text_tool", "", task)
    assert redirect == ""


@pytest.mark.parametrize("task,expect", [
    ("read my text messages", "view_messages"),
    ("show recent iMessages", "view_messages"),
    ("find texts from Sam", "view_messages"),
    ("show my recent texts", "view_messages"),
    ("count unread texts", "view_messages"),
    ("list texts Sam sent", "view_messages"),
    ("check for new texts", "view_messages"),
    ("list my contacts", "list_contacts"),
    ("summarize my inbox", "view_emails"),
])
def test_personal_data_requests_are_still_redirected(task, expect):
    redirect, _ = ta._capability_check("helper", "", task)
    assert expect in redirect


# --- b08-tools-rest#8: read_file and recursive list_dir are bounded ---------

def test_read_file_reads_only_a_prefix_of_a_huge_file(tmp_path, monkeypatch):
    from pathlib import Path

    from service.tools import builtin

    big = tmp_path / "huge.log"
    with big.open("wb") as fh:
        fh.write(b"first line\n")
        fh.truncate(2 * 1024 ** 3)  # sparse: no disk used
    # A NUL run would mean binary, so make the file look like text.
    with big.open("r+b") as fh:
        fh.seek(0)
        fh.write(b"a" * 100_000)

    def boom(self):
        raise AssertionError("read_bytes must not be used")

    monkeypatch.setattr(Path, "read_bytes", boom)
    out = builtin.read_file(str(big))
    assert out.startswith("a" * 100)
    assert len(out) < builtin.MAX_OUTPUT + 200
    assert "truncated" in out and "bytes" in out


def test_read_file_small_text_is_unchanged(tmp_path):
    from service.tools import builtin

    f = tmp_path / "note.txt"
    f.write_text("hello there\n")
    assert builtin.read_file(str(f)) == "hello there\n"


def test_read_file_binary_and_oversized_document(tmp_path, monkeypatch):
    from service.tools import builtin

    img = tmp_path / "pic.dat"
    img.write_bytes(b"\x89PNG\x00\x00" + b"x" * 50)
    assert "not text" in builtin.read_file(str(img))

    monkeypatch.setattr(builtin, "_MAX_DOCUMENT_BYTES", 10)
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + b"x" * 100)
    assert "too large" in builtin.read_file(str(pdf))


def test_recursive_list_dir_stops_near_the_output_cap(tmp_path, monkeypatch):
    import time as _t

    from service.tools import builtin

    for d in range(20):
        sub = tmp_path / f"folder{d:02d}"
        sub.mkdir()
        for i in range(120):
            (sub / f"a_rather_long_file_name_number_{i:04d}.txt").touch()
    walked = []
    real_walk = builtin.os.walk

    def counting_walk(*a, **k):
        for item in real_walk(*a, **k):
            walked.append(item[0])
            yield item

    monkeypatch.setattr(builtin.os, "walk", counting_walk)
    t0 = _t.monotonic()
    out = builtin.list_dir(str(tmp_path), recursive=True, max_depth=3)
    assert _t.monotonic() - t0 < 5
    assert "listing cut" in out
    assert len(out) < builtin.MAX_OUTPUT + 300
    assert len(walked) < 21  # stopped before visiting every folder


def test_recursive_list_dir_small_tree_has_no_cut_marker(tmp_path):
    from service.tools import builtin

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").touch()
    out = builtin.list_dir(str(tmp_path), recursive=True)
    assert out.splitlines() == ["a/", "a/b.txt"]


# --- b08-tools-rest#9: blocked folders get the permission message -----------

def test_list_dir_blocked_parent_gets_the_permission_message(tmp_path, monkeypatch):
    from service.tools import builtin

    blocked = tmp_path / "Documents"
    blocked.mkdir()
    real = builtin.os.listdir

    def fake(path="."):
        if str(path) == str(blocked):
            raise PermissionError(1, "Operation not permitted")
        return real(path)

    monkeypatch.setattr(builtin.os, "listdir", fake)
    out = builtin.list_dir(str(blocked / "Taxes"))
    assert "macOS blocked listing" in out
    assert "no such directory" not in out


def test_list_dir_really_missing_folder_still_lists_the_ancestor(tmp_path):
    from service.tools import builtin

    (tmp_path / "real").mkdir()
    out = builtin.list_dir(str(tmp_path / "nope"))
    assert out.startswith("(no such directory:")
    assert "real" in out


def test_find_files_blocked_parent_gets_the_permission_message(tmp_path, monkeypatch):
    from service.tools import files_tools

    blocked = tmp_path / "Documents"
    blocked.mkdir()
    real = files_tools.os.listdir

    def fake(path="."):
        if str(path) == str(blocked):
            raise PermissionError(1, "Operation not permitted")
        return real(path)

    monkeypatch.setattr(files_tools.os, "listdir", fake)
    out = files_tools.find_files("taxes", folder=str(blocked / "Taxes"))
    assert "macOS blocked" in out
    out = files_tools.find_files("taxes", folder=str(tmp_path / "missing"))
    assert "is not a folder on this Mac" in out


# --- b08-tools-rest#6: find_place category matching -------------------------

def _overpass_query(monkeypatch, what):
    import asyncio

    from service.tools import maps_travel as travel

    seen = []

    async def geocode(place):
        return (37.44, -122.14, "Palo Alto, California")

    async def get(url, **kwargs):
        seen.append(kwargs["params"]["data"])
        return {"elements": []}

    monkeypatch.setattr(travel, "_geocode", geocode)
    monkeypatch.setattr(travel, "_get", get)
    asyncio.run(travel.find_place(what, "Palo Alto"))
    return seen[0]


@pytest.mark.parametrize("what,want,not_want", [
    ("movie theater", "amenity=cinema", "restaurant"),
    ("barber", "shop=hairdresser", "amenity=bar"),
    ("barbecue", "name~", "amenity=bar"),
    ("barbershop", "shop=hairdresser", "amenity=bar"),
    ("barber shop", "shop=hairdresser", "amenity=bar"),
    ("veterinarian", "amenity=veterinary", "name~"),
    ("veterinary clinic", "amenity=veterinary", "name~"),
    ("coffeeshop", "amenity=cafe", "name~"),
    ("gas station", "amenity=fuel", "name~"),
    ("coffee shop", "amenity=cafe", "name~"),
    ("restaurants", "amenity=restaurant", "name~"),
    ("pharmacies", "amenity=pharmacy", "name~"),
    ("sushi", "name~", "amenity="),
])
def test_find_place_matches_whole_words(monkeypatch, what, want, not_want):
    q = _overpass_query(monkeypatch, what)
    assert want in q
    assert not_want not in q


# --- b08-tools-rest#10: text_to_speech argv and timeout ---------------------

def test_text_to_speech_puts_double_dash_before_text(monkeypatch):
    from service.tools import speech_tools

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(speech_tools.subprocess, "run", run)
    assert speech_tools.text_to_speech("- milk\n- eggs", voice="Samantha") == "Spoken."
    argv = calls[0][0]
    assert argv == ["say", "-v", "Samantha", "--", "- milk\n- eggs"]


def test_text_to_speech_wait_scales_with_length_and_timeout_is_a_sentence(monkeypatch):
    from service.tools import speech_tools

    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(speech_tools.subprocess, "run", run)
    out = speech_tools.text_to_speech("word " * 1000)
    assert isinstance(out, str) and "did not finish" in out
    assert seen[0] > 60
    assert speech_tools._timeout_for("short", False) == 60
    assert speech_tools._timeout_for("x" * 10**6, False) <= 600
    assert speech_tools._timeout_for("x" * 10**6, True) == 60


# --- tools audit 5.6: create_tool names, parameters, thinking, utilities -------

@pytest.mark.parametrize("task", [
    "validate an email address",
    "check email addresses in a csv file",
    "count the days between two calendar dates",
    "generate an ics calendar file from a title and a date",
    "print a reminder message with a timestamp",
])
def test_utilities_that_mention_mail_calendar_or_reminders_are_not_redirected(task):
    redirect, _ = ta._capability_check("helper", "", task)
    assert redirect == "", task


@pytest.mark.parametrize("task,expect", [
    ("read my emails", "view_emails"), ("list unread emails", "view_emails"),
    ("show my calendar for tomorrow", "get_upcoming"), ("list my upcoming events", "get_upcoming"),
    ("list my reminders", "add_reminder"), ("add a reminder", "add_reminder"),
])
def test_reading_the_users_mail_calendar_or_reminders_is_still_redirected(task, expect):
    redirect, _ = ta._capability_check("helper", "", task)
    assert expect in redirect, task


def test_a_parameter_marked_false_as_text_is_optional():
    clean, err = ta._validate_params([{"name": "a", "required": "false"},
                                      {"name": "b", "required": "true"},
                                      {"name": "c"}])
    assert not err and [p["required"] for p in clean] == [False, True, False]


def _isolated_skills(monkeypatch, tmp_path):
    from service import skills
    from service.skills import trust
    monkeypatch.setattr(ta, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(trust, "TRUST_PATH", tmp_path / "moe" / "skill_trust.json")
    (tmp_path / "skills").mkdir()


def test_a_built_in_name_is_refused_even_with_a_skill_folder_of_that_name(monkeypatch, tmp_path):
    import asyncio
    _isolated_skills(monkeypatch, tmp_path)
    (tmp_path / "skills" / "run_shell").mkdir()
    (tmp_path / "skills" / "run_shell" / "leftover.py").write_text("print('left')\n")

    async def never(*a, **k):
        raise AssertionError("no draft for a taken name")
    monkeypatch.setattr(ta, "_generate", never)
    err = asyncio.run(ta.prepare_draft({"name": "run_shell", "description": "d", "task": "t"}))
    assert "already called" in err


def test_a_leftover_folder_is_never_approved_with_a_new_tool(monkeypatch, tmp_path):
    import asyncio
    from service.skills import trust
    _isolated_skills(monkeypatch, tmp_path)
    leftover = tmp_path / "skills" / "fixture_leftover_tool"
    leftover.mkdir()
    (leftover / "helper.py").write_text("print('left')\n")

    async def never(*a, **k):
        raise AssertionError("no draft for a taken name")
    monkeypatch.setattr(ta, "_generate", never)
    assert "already exists" in asyncio.run(ta.prepare_draft(
        {"name": "fixture_leftover_tool", "description": "d", "task": "t"}))
    # A draft cached before the folder appeared is refused at install time too.
    ta._pending["fixture_leftover_tool"] = {"code": "print(1)\n", "description": "d", "parameters": [],
                                            "warning": "", "read_scopes": [], "write_scopes": [],
                                            "ts": __import__("time").time()}
    out = asyncio.run(ta.create_tool("fixture_leftover_tool", "d", "t", []))
    assert "already exists" in out
    assert trust._read() == {} and sorted(p.name for p in leftover.iterdir()) == ["helper.py"]


def test_drafting_asks_for_no_think_block_and_strips_one(monkeypatch):
    import asyncio
    from service.config.endpoints import Endpoint, Target
    sent: list[dict] = []

    class Fake:
        async def ensure_only(self, *a, **k):
            pass

        async def chat(self, model, messages, **kwargs):
            sent.append(kwargs)
            return {"choices": [{"message": {"content": "<think>plan the code</think>\nprint('ok')\n"}}]}

    target = Target("agent", Endpoint("local", "http://127.0.0.1:8000", "local_omlx", managed=True),
                    "fixture-model", context_window=8192)
    monkeypatch.setattr(ta, "role_target", lambda role: target)
    monkeypatch.setattr(ta, "_c", lambda: Fake())
    monkeypatch.setattr(ta, "no_thinking_kwargs",
                        lambda model: {"chat_template_kwargs": {"enable_thinking": False}})
    code, err = asyncio.run(ta._generate("fixture_tool", "print ok", []))
    assert not err and code == "print('ok')"
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
