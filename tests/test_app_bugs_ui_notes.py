"""UI review notes held in place by reading the Swift sources: labels and layout rules that no
compiled check reaches. No model, network or user data."""
from __future__ import annotations

from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "app" / "Sources" / "HarborApp"


def read(name: str) -> str:
    return (SOURCES / name).read_text(encoding="utf-8")


def test_the_writing_style_refresh_button_is_spoken_as_it_reads():
    source = read("WritingStyleSettingsRow.swift")
    button = source[source.index('Button("Refresh")'):]
    button = button[:button.index(".task")]
    assert '.accessibilityLabel("Refresh writing style status")' in button
    assert "Check whether" not in source


def test_the_calendar_toggle_says_what_adds_by_itself_and_what_waits_for_a_tap():
    source = read("CaughtForYou.swift")
    assert 'Toggle("Also add Calendar events (texts by themselves, email on your tap)"' in source


def test_the_quote_hint_moves_above_the_button_instead_of_wrapping_beside_it():
    source = read("SearchPanel.swift")
    card = source[source.index("private func citationSource"):source.index("private var notFoundCard")]
    fits = card[card.index("ViewThatFits(in: .horizontal)"):]
    assert "cantJumpHint.fixedSize(); Spacer(); copyQuote(c)" in fits      # one line when it fits
    assert "VStack(alignment: .leading" in fits                            # else the button goes below


def test_the_nudge_dismiss_hit_area_does_not_make_the_header_taller():
    source = read("NudgeViews.swift")
    button = source[source.index("Button(action: dismiss)"):source.index("if !nudge.body.isEmpty")]
    assert "Theme.iconButtonSize" in button
    assert ".padding(.vertical, -6)" in button


def test_the_tour_goes_back_to_its_first_page_under_a_plain_label():
    source = read("OnboardingTour.swift")
    assert '"Start over" : "Next"' in source
    assert source.count('.accessibilityHint("Example pages only; nothing is changed")') == 2
