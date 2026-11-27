"""Settings › Models: two menus (General use, Coding) that save several roles at once.
Invented model ids; no live server. (Intel: no built-in engine, so its model-folder listing is not carried.)"""
from pathlib import Path

import pytest
import yaml

from service import config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config, "USER_CONFIG", path)
    config.models_config.cache_clear()
    yield path
    config.models_config.cache_clear()


@pytest.fixture
def harbor(monkeypatch):
    from starlette.testclient import TestClient
    from service import main
    monkeypatch.setattr(main, "_sync_keep_warm", lambda: None)
    return TestClient(main.app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_one_save_moves_every_role_in_the_general_menu(overlay, harbor):
    r = harbor.post("/config", json={"roles": {"general": "alpha-7b", "fast": "alpha-7b", "reasoning": "alpha-7b"}})
    assert r.status_code == 200 and r.json()["ok"] is True
    roles = yaml.safe_load(overlay.read_text())["roles"]
    # general carries agent, fast carries router (set_roles' coupling).
    for role in ("general", "agent", "fast", "router", "reasoning"):
        assert roles[role] == "alpha-7b"
    assert "coding" not in roles


def test_a_coding_pick_leaves_general_alone(overlay, harbor):
    assert harbor.post("/config", json={"roles": {"coding": "Qwen3.5-9B-oQ4e-mtp"}}).json()["ok"] is True
    roles = yaml.safe_load(overlay.read_text())["roles"]
    assert roles == {"coding": "Qwen3.5-9B-oQ4e-mtp"}


@pytest.mark.parametrize("bad", [{"nonsense": "alpha"}, {"coding": ""}, {"coding": 3}])
def test_a_bad_group_saves_nothing(overlay, harbor, bad):
    r = harbor.post("/config", json={"roles": bad})
    assert r.json()["ok"] is False
    assert not overlay.exists()


def test_the_single_role_save_still_works(overlay, harbor):
    assert harbor.post("/config", json={"role": "coding", "model": "alpha-7b"}).json()["ok"] is True
    assert yaml.safe_load(overlay.read_text())["roles"]["coding"] == "alpha-7b"


def test_gemma_harbor_stays_default_and_no_thinking_capable():
    models = yaml.safe_load((ROOT / "service/config/models.yaml").read_text())
    assert "Gemma-Wisp" in models["no_thinking_capable"]
    text = (ROOT / "app/Sources/HarborApp/QuickSettingsText.swift").read_text()
    assert 'static let codingFirst = ["Qwen3.5-9B-oQ4e-mtp"]' in text
