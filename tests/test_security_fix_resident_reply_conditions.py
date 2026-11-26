"""Candidate review #5: the conditions resident_reply (mail_gists._resident_reply, the
background model path) depends on, pinned together:
- only an explicit remote-only profile makes the fast role non-local: the launcher's
  HARBOR_REMOTE_ONLY=1, or Settings' "Everything", which writes the profile AND
  remote_parity. A profile left in config.yaml alone, or one switched off, is not one;
- that profile's requests pass its strict payload check (any field outside its
  allowlist refused) and go through the cloud mask (a non-managed https endpoint);
- without remote_parity, the automatic background cycle stays paused.
A fictional example profile; sockets are forbidden and no Keychain item is read.
"""
from __future__ import annotations

import copy
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

if not os.environ.get("HARBOR_HOME") or Path(os.environ["HARBOR_HOME"]).resolve() == Path.home() / ".moe":
    raise RuntimeError("Use a fresh disposable HARBOR_HOME for remote-inference synthetic tests")

from service import config  # noqa: E402
from service.inference import remote_policy as policy  # noqa: E402
from service.proactive import cycle  # noqa: E402
from service.safety import cloud_mask  # noqa: E402
from tests.fixtures.remote_inference import EXAMPLE  # noqa: E402


@pytest.fixture
def world(monkeypatch):
    state = {"cfg": {}, "forced": None}
    monkeypatch.setattr(config, "models_config", lambda: state["cfg"])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("socket")))
    monkeypatch.delenv("HARBOR_REMOTE_BACKGROUND", raising=False)

    def set_mode(*, forced=None, profile=True, parity=False, enabled=True):
        prof = {**copy.deepcopy(EXAMPLE), "enabled": enabled}
        state["cfg"] = {"inference": {"remote_only": prof}} if profile else {}
        if parity:
            state["cfg"]["remote_parity"] = True
        policy.remote_only_profile.cache_clear()
        patcher = patch.object(policy, "_FORCED_MODE", forced)
        patcher.start()
        return patcher

    started = []
    yield lambda **kw: started.append(set_mode(**kw))
    for p in started:
        p.stop()
    policy.remote_only_profile.cache_clear()


@pytest.mark.parametrize("mode", [
    {"profile": True, "parity": False},                 # a profile left in config.yaml
    {"profile": True, "parity": True, "enabled": False},  # "Everything" switched off
    {"profile": False, "parity": True},                 # parity alone
])
def test_nothing_but_an_explicit_profile_moves_the_fast_role_off_this_mac(world, mode):
    world(**mode)
    assert policy.remote_only_profile() is None
    assert not policy.cloud_everything_active()


@pytest.mark.parametrize("mode", [{"forced": "1"}, {"profile": True, "parity": True}])
def test_the_explicit_profile_routes_fast_to_the_cloud_with_strict_checks_and_the_mask(world, mode):
    world(**mode)
    profile = policy.remote_only_profile()
    assert profile is not None
    assert config.role_to_model("fast") == profile.model_id
    target = policy.remote_default_target("fast")
    assert target.endpoint.managed is False and target.endpoint.base_url.startswith("https://")
    # The cloud mask applies to that endpoint...
    assert cloud_mask.is_cloud(managed=target.endpoint.managed, endpoint_name=target.endpoint.name,
                               base_url=target.endpoint.base_url)
    # ...and its payload check refuses any field outside the allowlist.
    payload = {"model": profile.model_id, "messages": [{"role": "user", "content": "fixture"}],
               "max_tokens": 64, "stream": False}
    profile.validate_payload(dict(payload))
    with pytest.raises(Exception):
        profile.validate_payload({**payload, "chat_template_kwargs": {"enable_thinking": False}})


@pytest.mark.parametrize("mode,paused", [
    ({"forced": "1"}, True),                            # remote-only without parity
    ({"forced": "1", "parity": True}, False),           # parity: behaves like a local model
    ({"profile": True, "parity": True}, False),         # Settings' "Everything"
    ({"profile": False}, False),                        # local: never paused for this reason
])
def test_without_remote_parity_the_automatic_cycle_stays_paused(world, mode, paused):
    world(**mode)
    assert cycle._remote_background_paused() is paused


def test_the_background_override_is_the_only_other_way_back_on(world, monkeypatch):
    world(forced="1")
    monkeypatch.setenv("HARBOR_REMOTE_BACKGROUND", "1")
    assert cycle._remote_background_paused() is False
