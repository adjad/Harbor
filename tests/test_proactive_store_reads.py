"""ProactiveStore never replaces a file it could not read: a transient read error is not
cached as {}, and a corrupt file is kept aside. Synthetic decisions in tmp_path only."""
from __future__ import annotations

import json
from pathlib import Path

from service.proactive.store import ProactiveStore


def _seed(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "decisions.json"
    path.write_text(json.dumps({"auto_added": {"fp-synthetic": {"at": 1e12, "identifier": "native-x"}}}))
    return path


def test_a_transient_read_error_never_wipes_the_file(tmp_path, monkeypatch):
    path = _seed(tmp_path / "proactive")
    store = ProactiveStore(tmp_path / "proactive")
    real = Path.read_text
    failing = {"on": True}

    def flaky(self, *args, **kwargs):
        if failing["on"] and self.name == "decisions.json":
            raise PermissionError("synthetic transient failure")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", flaky)
    store.auto_add_put("fp-new", {"at": 1e12, "identifier": "native-y"})   # a write during the failure
    assert "fp-synthetic" in json.loads(real(path))["auto_added"]
    failing["on"] = False
    assert "fp-synthetic" in store.auto_added()                          # retried, not a cached {}


def test_a_corrupt_file_is_kept_aside_and_a_missing_one_starts_empty(tmp_path):
    base = tmp_path / "proactive"
    base.mkdir()
    (base / "decisions.json").write_text("{not json")
    store = ProactiveStore(base)
    store.auto_add_put("fp-new", {"at": 1e12, "identifier": "native-y"})
    kept = list(base.glob("decisions.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{not json"
    assert "fp-new" in json.loads((base / "decisions.json").read_text())["auto_added"]
    fresh = ProactiveStore(tmp_path / "other")
    fresh.save_state(day="synthetic")
    assert json.loads((tmp_path / "other" / "state.json").read_text())["day"] == "synthetic"
