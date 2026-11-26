"""#16: research.db and its -wal/-shm sidecars are owner-only, sidecars an older
build left 0644 are tightened when the store opens, and Harbor's own folder is 0700
when it belongs to this user. A temp folder only; no real research data.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from service import paths
from service.research.store import ResearchStore


@pytest.fixture(autouse=True)
def open_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def _mode(path):
    return oct(os.lstat(path).st_mode & 0o777)


def test_a_new_store_and_its_sidecars_are_private(tmp_path):
    db = tmp_path / "moe" / "research.db"
    store = ResearchStore(db)
    store._db.execute("CREATE TABLE IF NOT EXISTS fixture (x)")
    store._db.execute("INSERT INTO fixture VALUES (1)")
    store._db.commit()
    for name in ("research.db", "research.db-wal", "research.db-shm"):
        target = db.with_name(name)
        if target.exists():
            assert _mode(target) == "0o600", name


def test_sidecars_left_open_by_an_older_build_are_tightened(tmp_path):
    db = tmp_path / "moe" / "research.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE fixture (x)")
    conn.commit()
    for name in ("research.db", "research.db-wal", "research.db-shm"):
        os.chmod(db.with_name(name), 0o644)
    ResearchStore(db)
    conn.close()
    for name in ("research.db-wal", "research.db-shm"):
        assert _mode(db.with_name(name)) == "0o600", name


def test_harbors_own_folder_is_made_0700(tmp_path):
    home = tmp_path / "moe"
    home.mkdir(mode=0o755)
    os.chmod(home, 0o755)
    paths.private_moe_dir(home)
    assert _mode(home) == "0o700"


def test_a_link_in_place_of_the_folder_is_left_alone(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.chmod(real, 0o755)
    (tmp_path / "moe").symlink_to(real)
    paths.private_moe_dir(tmp_path / "moe")
    assert _mode(real) == "0o755"
