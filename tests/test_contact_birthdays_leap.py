"""Feb 29 birthdays show in every year (on Feb 28 without a leap day) and never break the
tool after Feb 29 in a leap year. Made-up names; birthdays_raw and today are patched."""
from __future__ import annotations

import datetime

import pytest

from service.tools import contacts_tools as C


class _Day(datetime.date):
    pinned = datetime.date(2026, 2, 20)

    @classmethod
    def today(cls):
        return cls.pinned


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setattr(C, "birthdays_raw", lambda: {"02-29": ["Leap Synthetic"], "03-05": ["March Synthetic"]})
    monkeypatch.setattr(C.datetime, "date", _Day)

    def set_today(value):
        _Day.pinned = value
    return set_today


def test_feb_29_shows_on_feb_28_in_a_common_year(at):
    at(datetime.date(2026, 2, 20))
    out = C.contact_dates(days=30)
    assert "Leap Synthetic" in out and "February 28" in out


def test_after_feb_29_in_a_leap_year_the_tool_still_answers(at):
    at(datetime.date(2028, 3, 2))
    out = C.contact_dates(days=366)
    assert "March Synthetic" in out and "Leap Synthetic" in out
    at(datetime.date(2028, 2, 25))
    assert "February 29" in C.contact_dates(days=10)
