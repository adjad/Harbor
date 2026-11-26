"""#15: oMLX's settings.json holds the engine's key, so ensure_cap writes it only
through a private (0600) temp file and leaves nothing readable behind on a failure.
An invented settings file in tmp_path; the key in it is a placeholder.
"""
from __future__ import annotations

import json
import os

import pytest

from service import omlx_cache


def _settings(tmp_path, mode=0o600):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auth": {"api_key": "fixture-placeholder"},
                                "cache": {"ssd_cache_max_size": "auto"}}))
    os.chmod(path, mode)
    return path


@pytest.fixture(autouse=True)
def open_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def _leftovers(tmp_path):
    return [p for p in tmp_path.iterdir() if p.name != "settings.json"]


def test_a_failure_mid_write_leaves_no_readable_copy(tmp_path, monkeypatch):
    path = _settings(tmp_path)

    def boom(*_a, **_k):
        raise OSError("fixture failure")

    monkeypatch.setattr(os, "chmod", boom)
    monkeypatch.setattr(os, "fchmod", boom)
    assert omlx_cache.ensure_cap(path) is False
    for left in _leftovers(tmp_path):
        assert left.stat().st_mode & 0o077 == 0, (left.name, oct(left.stat().st_mode))


def test_the_temp_file_is_private_from_the_start(tmp_path, monkeypatch):
    path = _settings(tmp_path)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.stat(src).st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert omlx_cache.ensure_cap(path) is True
    assert seen == [0o600]
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["cache"]["ssd_cache_max_size"] == omlx_cache.CAP_TEXT
    assert not _leftovers(tmp_path)


def test_a_wider_file_is_tightened_and_a_planted_temp_link_is_not_followed(tmp_path):
    path = _settings(tmp_path, 0o644)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("untouched")
    (tmp_path / "settings.json.harbor-tmp").symlink_to(elsewhere)
    assert omlx_cache.ensure_cap(path) is True
    assert path.stat().st_mode & 0o777 == 0o600
    assert elsewhere.read_text() == "untouched"
