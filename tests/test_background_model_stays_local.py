"""Background model calls on mail (the inbox summary, mail memory, prewarm)
stand down for an engine that isn't on this Mac, and for a client that can't
say where it is (fail closed). Fake clients only: no engine, no network."""
import asyncio
import os
import tempfile

_scratch = tempfile.TemporaryDirectory(prefix="harbor-bg-local-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)
os.environ.setdefault("MOE_DIR", _scratch.name)

import pytest  # noqa: E402

import service.assistant.brief as brief  # noqa: E402
import service.inference.remote_policy as remote_policy  # noqa: E402


class Remote:
    managed = False
    base_url = "https://inference.example.net/v1"

    async def loaded_models(self):
        raise AssertionError("a non-local engine is never asked")

    async def chat(self, *a, **k):
        raise AssertionError("a non-local engine is never sent mail")


class Unknown(Remote):
    """No `managed` at all: where it runs is unknown."""
    managed = property(lambda self: (_ for _ in ()).throw(AttributeError("managed")))


@pytest.fixture(autouse=True)
def not_remote_only(monkeypatch):
    monkeypatch.setattr(remote_policy, "remote_only_profile", lambda: None)


@pytest.mark.parametrize("client", [Remote(), Unknown()])
def test_inbox_summary_stands_down_off_this_mac(monkeypatch, client):
    from service.assistant import mail_gists
    monkeypatch.setattr(brief, "_c", lambda: client)
    assert asyncio.run(mail_gists._resident_reply("sys", "mail", lambda: False, local_only=True)) is None


@pytest.mark.parametrize("client", [Remote(), Unknown()])
def test_mail_memory_stands_down_off_this_mac(monkeypatch, client):
    from service.mail_memory import runner
    monkeypatch.setattr(brief, "_c", lambda: client)
    assert asyncio.run(runner._stand_down()) == "not_local"


def test_mail_memory_accepts_a_loopback_app_and_harbors_own_engine():
    from service.mail_memory import runner
    app = type("App", (), {"managed": False, "base_url": "http://127.0.0.1:8000/v1"})()
    own = type("Own", (), {"managed": True, "base_url": "http://localhost:9/v1"})()
    assert runner._on_this_mac(app) and runner._on_this_mac(own)
    assert not runner._on_this_mac(Remote()) and not runner._on_this_mac(Unknown())


@pytest.mark.parametrize("client", [Remote(), Unknown()])
def test_prewarm_never_targets_an_engine_off_this_mac(client):
    from service import memory_saver
    assert asyncio.run(memory_saver.prewarm(client)) == "remote"
