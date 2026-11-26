"""oMLX prompt-cache budget: cap lowering and stale-file sweep."""
import json
import os
import stat
import time
from pathlib import Path

from service import omlx_cache as oc


def _settings(tmp_path, size):
    p = tmp_path / "settings.json"
    cache = {} if size is None else {"ssd_cache_max_size": size}
    p.write_text(json.dumps({"auth": {"api_key": "k"}, "cache": {**cache, "enabled": True}}))
    os.chmod(p, 0o600)
    return p


def test_cap_lowers_large_and_auto_values(tmp_path):
    for before in ("80GB", "auto", None, "4.5GB", "4GB"):
        p = _settings(tmp_path, before)
        assert oc.ensure_cap(p) is True
        data = json.loads(p.read_text())
        assert data["cache"]["ssd_cache_max_size"] == "2GB"
        assert data["cache"]["enabled"] is True and data["auth"]["api_key"] == "k"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_cap_keeps_smaller_user_value_and_survives_bad_file(tmp_path):
    p = _settings(tmp_path, "1GB")
    assert oc.ensure_cap(p) is False
    assert json.loads(p.read_text())["cache"]["ssd_cache_max_size"] == "1GB"
    p.write_text("not json")
    assert oc.ensure_cap(p) is False
    assert oc.ensure_cap(tmp_path / "missing.json") is False


H1, H2 = "a1" * 32, "b2" * 32      # made-up block hashes


def test_sweep_removes_only_stale_files(tmp_path):
    now = time.time()
    old, new = tmp_path / f"a/{H1}.safetensors", tmp_path / f"b/{H2}.safetensors"
    tmp_new, tmp_old = tmp_path / f"b/{H2}_tmp_0a1b2c3d.safetensors", tmp_path / f"b/{H1}_tmp_deadbeef.safetensors"
    for f, age_days in ((old, 10), (new, 1), (tmp_new, 0.1), (tmp_old, 3)):
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"x" * 10)
        t = now - age_days * 86400
        os.utime(f, (t, t))
    assert oc.sweep_stale(tmp_path, now=now) == (2, 20)
    assert not old.exists() and not tmp_old.exists()
    assert new.exists() and tmp_new.exists()
    assert oc.sweep_stale(tmp_path / "nowhere", now=now) == (0, 0)


def test_sweep_never_touches_files_that_are_not_omlx_blocks(tmp_path, monkeypatch):
    """ssd_cache_dir pointed at a shared folder: old documents there are not cache."""
    now, t = time.time(), time.time() - 30 * 86400
    foreign = [tmp_path / "Taxes 2024.pdf", tmp_path / "a/notes.txt", tmp_path / "photos/a/" / f"{H1}.safetensors",
               tmp_path / "a/model.safetensors", tmp_path / "vision_features/x.npy", tmp_path / "w.tmp"]
    for f in foreign:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"keep")
        os.utime(f, (t, t))
    block = tmp_path / f"c/{H1}.safetensors"
    block.parent.mkdir()
    block.write_bytes(b"x")
    os.utime(block, (t, t))
    assert oc.sweep_stale(tmp_path, now=now) == (1, 1)
    assert all(f.exists() for f in foreign) and not block.exists()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "c" / f"{H2}.safetensors").write_bytes(b"x")
    os.utime(tmp_path / "c" / f"{H2}.safetensors", (t, t))
    assert oc.sweep_stale(Path("."), now=now) == (0, 0)    # a relative root is refused


def test_trim_vision_evicts_least_recently_used_first(tmp_path):
    now = time.time()
    names = ("oldest", "middle", "newest")
    for i, name in enumerate(names):
        f = tmp_path / "c" / f"{name}.safetensors"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(b"x" * 100)
        t = now - (3 - i) * 3600
        os.utime(f, (t, t))
    assert oc.trim_vision(tmp_path, max_bytes=150) == (2, 200)
    assert [p.name for p in tmp_path.rglob("*.safetensors")] == ["newest.safetensors"]
    assert oc.trim_vision(tmp_path, max_bytes=150) == (0, 0)
    assert oc.trim_vision(tmp_path / "nowhere") == (0, 0)
