"""verify(): an uncited sentence is kept when one retrieved passage accounts for
its content words, and cited to that passage.

Measured 2026-10-13 on Ling-3.0-tiny-Wisp-V3-merged-oQ4e: 6/6 Smart Search
answers correct, 3/6 carrying a [n], the other three dropped as "uncited" —
a right answer shown as "not found" half the time. The provenance guarantee
stays: nothing is kept that cannot be traced to a passage.
"""
from __future__ import annotations

from service.search.chunker import Chunk
from service.search.synth import NOT_FOUND, verify

P1 = ("Flour costs rose 14 percent after the March supplier change; the team "
      "switched from Harvest Mills to Prairie Grain on March 18.")
P2 = ("Open risk: the lease on the Elm Avenue kitchen expires on September 30 "
      "and the landlord has not confirmed renewal terms.")
P3 = "Next week: inhibitor screen at 37 C using lot E-2291 only, triplicates."
CHUNKS = [Chunk(idx=i, text=t, start=0, end=len(t)) for i, t in enumerate([P1, P2, P3])]
PICKS = [0, 1, 2]


def test_model_citation_is_untouched():
    text, used = verify("They switched to Prairie Grain on March 18 [1].", CHUNKS, PICKS)
    assert text == "They switched to Prairie Grain on March 18 [1]."
    assert used == [0]


def test_uncited_paraphrase_is_grounded_and_cited():
    text, used = verify(
        "The open risk is that the lease on the Elm Avenue kitchen expires on "
        "September 30 and the landlord has not confirmed renewal terms.", CHUNKS, PICKS)
    assert text.endswith("renewal terms [2].")
    assert used == [1]


def test_short_exact_answer_is_grounded_only_on_a_full_match():
    text, used = verify("37 C.", CHUNKS, PICKS)
    assert text == "37 C [3]."
    assert used == [2]
    # A short answer with a token the passages don't contain is still dropped.
    assert verify("42 C.", CHUNKS, PICKS) == (NOT_FOUND, [])


def test_sentence_about_something_else_is_still_dropped():
    assert verify("The bakery opened a new branch in Boston last year.", CHUNKS, PICKS) == (NOT_FOUND, [])


def test_mixed_answer_keeps_grounded_sentences_and_drops_the_rest():
    text, used = verify(
        "Lot E-2291 is used for the inhibitor screen at 37 C. "
        "The CEO announced record profits.", CHUNKS, PICKS)
    # verify() cites by passage number; _answer renumbers to first-use order later.
    assert "inhibitor screen at 37 C [3]." in text
    assert "record profits" not in text
    assert used == [2]


def test_wrong_figure_is_never_cited():
    # Audit case: a changed date or percentage must not come back cited.
    assert verify("They switched to Prairie Grain on March 19.", CHUNKS, PICKS) == (NOT_FOUND, [])
    assert verify("Flour costs rose 40 percent after the March supplier change.", CHUNKS, PICKS) == (NOT_FOUND, [])
    # The true figures still ground.
    text, used = verify("Flour costs rose 14 percent after the March supplier change.", CHUNKS, PICKS)
    assert used == [0] and text.endswith("[1].")


def test_inverted_negation_is_never_cited():
    # Audit case: "has confirmed" against "has not confirmed".
    assert verify("The landlord has confirmed renewal terms.", CHUNKS, PICKS) == (NOT_FOUND, [])
    text, used = verify("The landlord has not confirmed renewal terms.", CHUNKS, PICKS)
    assert used == [1]


def test_true_clause_with_an_invented_one_is_dropped():
    # Audit case: scored 75% under the first version's 60% threshold.
    assert verify("They switched to Prairie Grain on March 18 and the CEO resigned in disgrace.",
                  CHUNKS, PICKS) == (NOT_FOUND, [])


def test_trailing_period_on_a_figure_does_not_break_matching():
    text, used = verify("The lease on the Elm Avenue kitchen expires on September 30.", CHUNKS, PICKS)
    assert used == [1]


def test_known_limit_entity_swap_is_documented():
    """Two entities both present in the passage, swapped, still ground: this
    is lexical overlap's blind spot and the same gap the model's own citations
    have always had. Recorded here so a future semantic check has a target."""
    text, used = verify("They switched from Prairie Grain to Harvest Mills on March 18.", CHUNKS, PICKS)
    assert used == [0]


def test_global_scope_behavior_is_unchanged():
    # Global scope already kept uncited sentences; grounding must not start
    # adding citations there.
    text, used = verify("The document is a lab notebook about enzyme assays.",
                        CHUNKS, PICKS, scope="global")
    assert "[" not in text
    assert used == []


def test_contracted_negation_matches_the_spelled_out_passage():
    # P2 says "has not confirmed renewal terms"; the contraction is the same claim.
    text, used = verify("The landlord hasn't confirmed renewal terms.", CHUNKS, PICKS)
    assert text.endswith("[2].") and used == [1]


def test_contracted_negation_is_not_cited_to_a_positive_claim():
    chunks = [Chunk(idx=0, text="The supplier didn't ship the lot on March 18.", start=0, end=45)]
    assert verify("The supplier shipped the lot on March 18.", chunks, [0]) == (NOT_FOUND, [])
    text, _ = verify("The supplier didn't ship the lot on March 18.", chunks, [0])
    assert "didn't" in text


def test_cant_is_a_negation():
    chunks = [Chunk(idx=0, text="The team cannot approve refunds above 500 dollars.", start=0, end=50)]
    text, _ = verify("The team can't approve refunds above 500 dollars.", chunks, [0])
    assert text.startswith("The team can't")


def test_quotation_spanning_sentences_is_kept_whole():
    src = 'The memo ends: "We ship Friday. Nobody leaves early." Signed, ops.'
    chunks = [Chunk(idx=0, text=src, start=0, end=len(src))]
    text, used = verify('The memo says "We ship Friday. Nobody leaves early." [1]', chunks, [0])
    assert "Nobody leaves early" in text and used == [0]
    # A fabricated second half is still dropped.
    assert verify('The memo says "We ship Friday. Everyone stays late." [1]',
                  chunks, [0]) == (NOT_FOUND, [])


def test_abbreviation_does_not_split_a_sentence():
    chunks = [Chunk(idx=0, text="Dr. Rivera approved the budget on March 3.", start=0, end=42)]
    text, _ = verify("Dr. Rivera approved the budget on March 3 [1].", chunks, [0])
    assert text == "Dr. Rivera approved the budget on March 3 [1]."


def test_global_scope_keeps_an_inflected_name_but_not_an_invented_one():
    src = "Rivera led the Romans through the winter; Marta kept the ledger."
    chunks = [Chunk(idx=0, text=src, start=0, end=len(src))]
    kept, _ = verify("The story follows a Roman general and Marta.", chunks, [0], scope="global")
    assert "Marta" in kept
    kept, _ = verify("Their leader was Romans-born Rivera, praised by Quentin.",
                     chunks, [0], scope="global")
    assert kept == NOT_FOUND
