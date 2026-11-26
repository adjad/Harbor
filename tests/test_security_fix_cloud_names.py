"""#18: a request to a cloud model leaves with the user's own name and their contacts'
names masked, not only emails and numbers, and the reply comes back with them restored.
Invented names only; the name sources are replaced, nothing reads the real Contacts.
"""
from __future__ import annotations

import pytest

from service.safety import cloud_mask

NAMES = ["Fixture Userperson", "Maya Inventedname", "Orrin Placeholder"]


@pytest.fixture(autouse=True)
def invented_sources(monkeypatch):
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: list(NAMES), raising=False)
    monkeypatch.setattr(cloud_mask, "_names_cache", (0.0, ()), raising=False)
    cloud_mask.forget()
    yield
    cloud_mask.forget()


def test_a_conversation_masker_masks_known_names_and_first_names():
    masker = cloud_mask.masker_for("fixture-conversation")
    text = "Maya Inventedname wrote to Fixture. Orrin said Maya will call."
    masked = masker.mask(text)
    for name in ("Maya", "Inventedname", "Fixture", "Orrin"):
        assert name not in masked, masked
    assert masker.unmask(masked) == text


def test_a_common_word_first_name_is_not_masked_on_its_own(monkeypatch):
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: ["May Fixturesurname"])
    masker = cloud_mask.masker_for("fixture-month")
    masked = masker.mask("May Fixturesurname is free in May.")
    assert "Fixturesurname" not in masked and masked.endswith("free in May.")


def test_names_learned_after_the_conversation_started_are_added(monkeypatch):
    masker = cloud_mask.masker_for("fixture-later")
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: NAMES + ["Quill Latecomer"])
    monkeypatch.setattr(cloud_mask, "_names_cache", (0.0, ()), raising=False)
    masker = cloud_mask.masker_for("fixture-later")
    assert "Quill" not in masker.mask("Quill Latecomer replied.")


def test_a_background_cloud_call_masks_names_too():
    from service.inference.omlx_client import OMLXClient

    class Remote:
        managed = False
        endpoint_name = "fixture_cloud"
        base_url = "https://cloud.example.invalid/v1"

    masker = OMLXClient._cloud_masker(Remote())
    assert masker is not None and "Orrin" not in masker.mask("Ask Orrin Placeholder about it.")


def test_a_full_name_in_any_case_is_masked_and_comes_back_exactly():
    masker = cloud_mask.masker_for("fixture-case")
    text = "maya inventedname said MAYA INVENTEDNAME would come"
    masked = masker.mask(text)
    assert "inventedname" not in masked.casefold()
    assert masker.unmask(masked) == text


def test_ordinary_lowercase_words_that_are_name_parts_are_left_alone(monkeypatch):
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: ["Jordan Long", "Pat Cook"])
    masker = cloud_mask.masker_for("fixture-words")
    text = "long story short, cook dinner with Jordan Long"
    masked = masker.mask(text)
    assert masked.startswith("long story short, cook dinner with [NAME_")
    assert masker.unmask(masked) == text


def test_a_person_with_a_surname_like_bank_keeps_part_masking(monkeypatch):
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: ["Samfix Bank"])
    masker = cloud_mask.masker_for("fixture-bank")
    assert "Samfix" not in masker.mask("Samfix called.")


def test_an_organisation_contact_masks_only_its_full_name(monkeypatch):
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: ["Fixture Bank of Examplia"])
    masker = cloud_mask.masker_for("fixture-org")
    masked = masker.mask("Fixture Bank of Examplia wrote. Examplia is a place.")
    assert "Fixture Bank of Examplia" not in masked and "Examplia is a place." in masked


def test_an_empty_name_list_is_cached_only_briefly(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_mask, "_name_sources", lambda: calls.append(1) or [])
    cloud_mask.known_names()
    cloud_mask.known_names()
    assert len(calls) == 1
    stamp, names = cloud_mask._names_cache
    monkeypatch.setattr(cloud_mask, "_names_cache", (stamp - cloud_mask._EMPTY_NAMES_TTL_S - 1, names))
    cloud_mask.known_names()
    assert len(calls) == 2
