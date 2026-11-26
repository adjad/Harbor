"""#21: a shell command can't reach the local engines either: oMLX on 8000, the bundled
engine, a custom oMLX port and a configured local provider. A remote provider's port
is not a local service and is left alone. Ports and URLs are invented.
"""
from __future__ import annotations

import pytest

from service import config
from service.engine.manager import BUNDLED_PORT
from service.safety import shell_sandbox


def _denied(profile: str) -> set[int]:
    return {int(line.rsplit(":", 1)[1].rstrip('"))'))
            for line in profile.splitlines() if line.startswith("(deny network-outbound")}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "omlx_base_url", lambda: "http://127.0.0.1:8123")
    monkeypatch.setattr(config, "local_provider_settings",
                        lambda: {"base_url": "http://localhost:8799/v1", "enabled": True})


def test_the_engine_ports_are_denied(configured):
    denied = _denied(shell_sandbox.build_profile())
    assert {8765, 8000, BUNDLED_PORT, 8123, 8799} <= denied


def test_a_remote_provider_port_is_not_a_local_service(monkeypatch):
    monkeypatch.setattr(config, "omlx_base_url", lambda: "https://engine.example.invalid:9443")
    monkeypatch.setattr(config, "local_provider_settings", lambda: {"base_url": "https://example.invalid:9555"})
    denied = _denied(shell_sandbox.build_profile())
    assert 9443 not in denied and 9555 not in denied and 8000 in denied


def test_an_unreadable_config_still_denies_the_fixed_ports(monkeypatch):
    def boom():
        raise RuntimeError("fixture config failure")

    monkeypatch.setattr(config, "omlx_base_url", boom)
    monkeypatch.setattr(config, "local_provider_settings", boom)
    assert {8765, 8000, BUNDLED_PORT} <= _denied(shell_sandbox.build_profile())
