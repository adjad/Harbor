"""Tools audit, section 4 (system tools): every fix with invented data and a
mocked subprocess. Nothing here touches this Mac's settings, apps or windows."""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from service.tools import (apps, automation_tools, browser_history_tools as bh,
                           conversions as cv, doc_tools, speech_tools,
                           system_control, system_extras, system_extras2,
                           timers_alarms as ta, window_tools)


def _cp(argv=(), rc=0, out="", err=""):
    return subprocess.CompletedProcess(list(argv), rc, out, err)


def _raise_timeout(*a, **kw):
    raise subprocess.TimeoutExpired(a[0] if a else "x", 1)


# --- 4.3 directory targets --------------------------------------------------
def test_write_document_into_a_folder_uses_a_default_name(tmp_path):
    out = doc_tools.write_document(str(tmp_path), "Trip Plan", ["one"])
    assert (tmp_path / "Trip Plan.docx").exists()
    assert not Path(str(tmp_path) + ".docx").exists()
    assert "Trip Plan.docx" in out


def test_spreadsheet_ops_into_a_folder_uses_a_default_name(tmp_path):
    spreadsheet = doc_tools.spreadsheet_ops(str(tmp_path) + "/", ["a"], [[1]])
    assert (tmp_path / "Spreadsheet.xlsx").exists()
    assert not Path(str(tmp_path) + ".xlsx").exists()
    assert "Spreadsheet.xlsx" in spreadsheet


def test_text_to_speech_into_a_folder_saves_inside_it(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(speech_tools.subprocess, "run",
                        lambda argv, **kw: seen.setdefault("argv", argv) and _cp(argv))
    out = speech_tools.text_to_speech("hello", save_to=str(tmp_path))
    assert seen["argv"][seen["argv"].index("-o") + 1] == str(tmp_path / "Speech.aiff")
    assert "Speech.aiff" in out


# --- 4.4 calculate / convert_units -----------------------------------------
def test_huge_int_is_a_clean_error():
    out = cv.calculate("100000 ** 1000")
    assert out.startswith("(error")


@pytest.mark.parametrize("expr", ["[1]*1000000000", "(1,2)*1000000000",
                                  "1000000000*[1]", "-[1]"])
def test_container_arithmetic_is_refused(expr):
    assert cv.calculate(expr).startswith("(error")


def test_commas_in_lists_are_not_joined():
    assert cv.calculate("sum([1,200])") == "201"
    assert cv.calculate("max(1,200)") == "200"


def test_thousands_separators_still_work():
    assert cv.calculate("1,200 + 5") == "1,205"
    assert cv.calculate("20% of 1,250") == "250"


def test_two_percent_phrases_in_one_expression():
    assert cv.calculate("20% of 100, 30% of 200") == "20, 60"


def test_tiny_numbers_do_not_round_to_zero():
    assert cv.calculate("1/100000000000") != "0"
    assert "e-11" in cv.calculate("1/100000000000")
    assert cv.calculate("0") == "0"


@pytest.mark.parametrize("value,src,dst,expect", [
    (1, "kilogram", "gram", "1,000"),
    (2500, "gram", "kilogram", "2.5"),
    (1, "kilometer", "meter", "1,000"),
    (1, "pint", "quart", "0.5"),
    (1, "gigabyte", "megabyte", "1,000"),
    (1, "quart", "fl oz", "32"),
])
def test_more_unit_names(value, src, dst, expect):
    assert f"= {expect} " in cv.convert_units(value, src, dst)


def test_singular_degree_celsius():
    assert "212" in cv.convert_units(100, "degree celsius", "degree fahrenheit")
    assert "212" in cv.convert_units(100, "degrees Celsius", "°F")


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf")])
def test_convert_units_rejects_bool_nan_inf(value):
    assert cv.convert_units(value, "m", "km").startswith("(error")


# --- 4.5 timers -------------------------------------------------------------
def test_zero_duration_is_zero_not_none():
    assert ta._parse_duration("0s") == 0.0
    assert ta._parse_duration("0 minutes") == 0.0


@pytest.mark.parametrize("text", ["-5 minutes", "-5m", "-90s"])
def test_negative_duration_is_not_positive(text):
    assert ta._parse_duration(text) < 0


@pytest.fixture
def quiet_timers(tmp_path, monkeypatch):
    monkeypatch.setattr(ta, "_STORE", tmp_path / "timers.json")
    monkeypatch.setattr(ta, "_STOPWATCH_STORE", tmp_path / "stopwatch.json")
    monkeypatch.setattr(ta, "_TIMERS", {})
    monkeypatch.setattr(ta, "_STOPWATCH", {})
    monkeypatch.setattr(ta, "_arm", lambda tid: None)
    return ta


def test_negative_timer_is_rejected(quiet_timers):
    out = str(asyncio.run(ta.set_timer(kind="timer", duration="-5 minutes")))
    assert "zero or negative" in out and not ta._TIMERS


def test_zero_timer_is_rejected_as_zero(quiet_timers):
    out = str(asyncio.run(ta.set_timer(kind="timer", duration="0s")))
    assert "zero or negative" in out and not ta._TIMERS


def test_sleep_timer_keeps_its_label(quiet_timers):
    asyncio.run(ta.set_timer(kind="sleep", duration="10 minutes", label="Podcast"))
    assert [t["label"] for t in ta._TIMERS.values()] == ["Podcast"]
    ta._TIMERS.clear()
    asyncio.run(ta.set_timer(kind="sleep", duration="10 minutes"))
    assert [t["label"] for t in ta._TIMERS.values()] == ["Sleep timer"]


def test_sleep_timer_honours_the_24_hour_cap(quiet_timers):
    out = str(asyncio.run(ta.set_timer(kind="sleep", duration="25 hours")))
    assert "24 hours" in out and not ta._TIMERS


def test_alarm_rejects_hour_13_with_am_pm(quiet_timers):
    for bad in ("13:00am", "13:00pm", "0:30am"):
        assert "valid clock time" in str(ta._set_alarm(bad)), bad
    assert not ta._TIMERS
    assert str(ta._set_alarm("12:30am")).startswith("Alarm set")
    assert str(ta._set_alarm("13:00")).startswith("Alarm set")


def test_stopwatch_survives_a_restart(quiet_timers):
    assert ta._stopwatch("start") == "Stopwatch started."
    ta._stopwatch("lap")
    ta._STOPWATCH.clear()                       # a new backend process
    assert "elapsed" in ta._stopwatch("read")
    ta._STOPWATCH.clear()
    assert "(1 laps)" in ta._stopwatch("stop")
    ta._STOPWATCH.clear()
    assert "isn't running" in ta._stopwatch("read")


# --- 4.6 apps, windows, system ---------------------------------------------
@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        return _cp(argv)
    for mod in (apps, window_tools, system_control, system_extras2):
        monkeypatch.setattr(mod.subprocess, "run", fake)
    return calls


def test_open_app_puts_double_dash_before_the_target(ran):
    apps.open_app("Notes", "-W")
    assert ran[-1] == ["open", "-a", "Notes", "--", "-W"]


def test_open_app_expands_home(ran):
    apps.open_app("Notes", "~/Documents/x.txt")
    assert ran[-1][-1] == str(Path("~/Documents/x.txt").expanduser())


def test_open_app_leaves_urls_alone(ran):
    apps.open_app("Safari", "https://example.com/a")
    assert ran[-1][-2:] == ["--", "https://example.com/a"]


def test_open_app_refuses_a_wrong_account_path(ran):
    out = apps.open_app("Notes", "/Users/nobody-here-xyz/file.txt")
    assert out.startswith("(error") and not ran


def test_quit_app_uses_the_aliases(ran):
    apps.quit_app("Apple TV")
    assert ran[-1][-1] == 'tell application "TV" to quit'


def test_timeouts_become_messages(monkeypatch):
    for mod in (apps, window_tools, system_control, automation_tools):
        monkeypatch.setattr(mod.subprocess, "run", _raise_timeout)
    assert apps.open_app("Notes").startswith("(could not")
    assert apps.quit_app("Notes").startswith("(could not")
    assert window_tools.window_control("close").startswith("(error")
    assert window_tools.list_running_apps().startswith("(error")
    assert system_control.clipboard_read().startswith("(could not")
    assert system_control.battery_status().startswith("(no battery")
    monkeypatch.setattr("service.safety.policy.decide",
                        lambda *a, **k: SimpleNamespace(tier=None, reason=""))
    assert "did not finish" in automation_tools.run_applescript("beep")


def test_tiling_uses_the_screens_visible_frame(ran):
    window_tools.window_control("left")
    left = ran[-1][-1]
    window_tools.window_control("right")
    right = ran[-1][-1]
    assert "visibleFrame" in left and "NSScreen" in left
    assert "menuBar to 25" not in left
    assert "set position of win to {vx, vy}" in left
    assert "set position of win to {vx + vw / 2, vy}" in right
    assert "set size of win to {vw / 2, vh}" in left


def _ports(monkeypatch, listing, ips=None):
    ips = ips or {}
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["networksetup", "-listallhardwareports"]:
            return _cp(argv, out=listing)
        if argv[:2] == ["ipconfig", "getifaddr"]:
            return _cp(argv, rc=0 if argv[2] in ips else 1, out=ips.get(argv[2], ""))
        if argv[:2] == ["networksetup", "-getairportnetwork"]:
            return _cp(argv, out="Current Wi-Fi Network: HomeNet")
        return _cp(argv)
    for mod in (system_control, system_extras2):
        monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    return calls


LISTING = """Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: aa

Hardware Port: Wi-Fi
Device: en1
Ethernet Address: bb

Hardware Port: Thunderbolt Bridge
Device: bridge0
"""


def test_wifi_power_uses_the_real_wifi_interface(monkeypatch):
    calls = _ports(monkeypatch, LISTING)
    system_control.wifi_power(False)
    assert calls[-1] == ["networksetup", "-setairportpower", "en1", "off"]


def test_wifi_power_on_a_mac_without_wifi(monkeypatch):
    calls = _ports(monkeypatch, "Hardware Port: Ethernet\nDevice: en0\n")
    assert "no Wi-Fi" in system_control.wifi_power(True)
    assert not any("-setairportpower" in c for c in calls)


def test_network_details_reports_wifi_and_ethernet(monkeypatch):
    _ports(monkeypatch, LISTING, {"en1": "192.0.2.10", "en4": "192.0.2.20"})
    out = system_extras2.network_details()
    assert "Local IP (Wi-Fi): 192.0.2.10" in out
    assert "Ethernet, en4): 192.0.2.20" in out
    assert "HomeNet" in out


def test_toggle_setting_does_not_advertise_root_only_settings():
    from service.tools.registry import REGISTRY
    spec = REGISTRY["toggle_setting"]
    schema = getattr(spec, "parameters", None) or getattr(spec, "schema", None)
    enum = schema["properties"]["setting"]["enum"]
    for name in ("bluetooth", "low_power_mode", "firewall"):
        assert name not in enum
    assert "admin password" in spec.description
    assert "unknown_thing" not in system_extras._SETTINGS
    assert "firewall" in system_extras.toggle_setting("nonsense", True)


def test_auto_appearance_does_not_force_dark_first(monkeypatch):
    scripts = []
    monkeypatch.setattr(system_extras2, "_osa", lambda s: scripts.append(s) or _cp())
    assert system_extras2.appearance_mode("auto") == "Appearance set to auto."
    assert "dark mode" not in scripts[0]
    assert "AppleInterfaceStyleSwitchesAutomatically -bool true" in scripts[0]


def test_screen_capture_escape_with_an_existing_file_is_cancelled(monkeypatch, tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(b"old")
    monkeypatch.setattr(system_extras, "_run", lambda argv, **kw: _cp(argv))
    out = system_extras.screen_capture("selection", str(target))
    assert "cancelled" in out and "Saved" not in out


def test_screen_capture_overwrite_is_reported_as_saved(monkeypatch, tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(b"old")

    def fake(argv, **kw):
        target.write_bytes(b"new image data")
        return _cp(argv)
    monkeypatch.setattr(system_extras, "_run", fake)
    assert system_extras.screen_capture("selection", str(target)).startswith("Saved")


# --- 4.7 browser history ----------------------------------------------------
def test_browser_history_count_is_clamped_to_one(monkeypatch):
    async def noop(sources):
        return None
    monkeypatch.setattr("service.assistant.sync_status.ensure_sources", noop)
    monkeypatch.setattr(bh, "browser_history_sync_state", lambda: "ready")
    now = time.time()
    rows = [{"ts": now - i, "host": f"h{i}.example.com", "path": "/", "title": "",
             "browser": "safari"} for i in range(3)]
    monkeypatch.setattr(bh, "_all_rows", lambda: list(rows))
    for count in (-1, 0):
        out = asyncio.run(bh.search_browser_history_impl(None, count, None, None))
        assert len(out.splitlines()) == 1, (count, out)
