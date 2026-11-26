"""#9: the credential scrubber stays linear on a long run of short "ab-" segments, and
long text is scrubbed off the event loop. #19: common token shapes it used to miss are
redacted. Every value here is invented and has the shape of a token only.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from service.safety import redaction as R

FIXTURE_TAIL = "Fx7Kq2Lm9Np4Rs8Tv1Wx6Yz3Ab5Cd0Ef"   # invented, 32 characters


def test_a_long_run_of_short_segments_is_not_quadratic():
    started = time.monotonic()
    R.scrub("ab-" * 8000)
    assert time.monotonic() - started < 0.2


@pytest.mark.parametrize("token", [
    "ya29." + FIXTURE_TAIL,                       # Google OAuth access token shape
    "1//0g" + FIXTURE_TAIL,                       # Google OAuth refresh token shape
    "sk_live_" + FIXTURE_TAIL,                    # Stripe secret key shapes
    "sk_test_" + FIXTURE_TAIL,
    "rk_live_" + FIXTURE_TAIL,
    "npm_" + FIXTURE_TAIL + "ab12",               # npm token: 36 after the prefix
    "glpat-" + FIXTURE_TAIL[:20],                 # GitLab personal access token
    "xoxc-" + "1234-5678-" + FIXTURE_TAIL,        # Slack browser tokens
    "xoxd-" + FIXTURE_TAIL,
])
def test_each_token_shape_is_redacted(token):
    cleaned, count = R.scrub(f"the fixture value is {token} for the invented service")
    assert token not in cleaned and count == 1, cleaned


def test_an_authorization_basic_header_is_redacted():
    header = "Authorization: Basic Zml4dHVyZS11c2VyOmZpeHR1cmUtcGFzcw=="
    cleaned, count = R.scrub(f"curl -H '{header}' https://example.invalid/")
    assert "Zml4dHVyZS11c2VyOmZpeHR1cmUtcGFzcw" not in cleaned and count == 1


@pytest.mark.parametrize("text", [
    "Basic internationalization support is on the roadmap for the fixture app.",
    "The task_live_counter and risk_test_results tables are fine.",
    "Clone https://example.invalid/group/project.git at 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
])
def test_ordinary_text_is_left_alone(text):
    assert R.scrub(text) == (text, 0)


def test_long_qualified_labels_still_count():
    text = "fixture_service_client_secret=" + FIXTURE_TAIL
    cleaned, count = R.scrub(text)
    assert FIXTURE_TAIL not in cleaned and count == 1


def test_long_text_is_scrubbed_off_the_event_loop(monkeypatch):
    seen = []
    real = asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        seen.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    long_text = "fixture words " * 2000 + "sk_live_" + FIXTURE_TAIL
    cleaned, count = asyncio.run(R.scrub_async(long_text))
    assert count == 1 and seen == [R.scrub]
    short, _ = asyncio.run(R.scrub_async("short fixture"))
    assert short == "short fixture" and len(seen) == 1


def test_a_label_with_many_qualifiers_still_counts():
    text = "fixture_a_b_c_d_e_api_key=" + FIXTURE_TAIL
    cleaned, count = R.scrub(text)
    assert FIXTURE_TAIL not in cleaned and count == 1


def test_the_bound_stays_linear_on_underscore_runs_too():
    started = time.monotonic()
    R.scrub("ab_" * 20000 + "=x")
    assert time.monotonic() - started < 0.3
