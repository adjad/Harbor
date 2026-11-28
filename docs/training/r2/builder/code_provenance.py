#!/usr/bin/env python3
"""Which code a step really ran on. Every stats.json records its own step's code; none copies an earlier step's head.

``code``: the training repo's HEAD, and whether training/ had uncommitted code. When it did, ``code_dirty_sha256`` hashes the
diff against HEAD plus each untracked .py/.sh file under training/, so two dirty runs can be told apart, and the head alone
never claims a commit the run didn't match.

``service``: the Harbor code that shaped prompts and tools. When HARBOR_REPO is the repo itself, it's the last commit touching
service/ plus a dirty flag. When it's a pinned copy such as ``data/pin-7e442f2d``, every file's blob is compared with that
commit: ``service_head`` is set only if service/ matches it file for file, and files that differ are listed by path and blob.

With no git (a node with only the kit), ``CODE_PROVENANCE.json`` beside the scripts is used if present, else the head is
``unknown``. Nothing is inherited.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

CODE_SUFFIXES = (".py", ".sh")
KIT_FILE = "CODE_PROVENANCE.json"


def _git(cwd, *args, stdin: str | None = None) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, input=stdin, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def code(code_dir) -> dict:
    code_dir = Path(code_dir).resolve()
    top = (_git(code_dir, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        kit = code_dir / KIT_FILE
        if kit.is_file():
            return json.loads(kit.read_text()) | {"code_source": f"kit {KIT_FILE}"}
        return {"code_head": "unknown", "code_source": "no git and no kit provenance"}
    head = (_git(top, "rev-parse", "HEAD") or "").strip() or "unknown"
    tree = _training_tree(code_dir, top)
    changed = [ln[3:] for ln in (_git(top, "status", "--porcelain=v1", "--untracked-files=all", "--", tree) or "").splitlines()
               if ln[:2] != "??" or ln[3:].endswith(CODE_SUFFIXES)]
    out = {"code_head": head, "code_dirty": bool(changed), "code_source": "git"}
    if changed:
        h = hashlib.sha256((_git(top, "diff", "HEAD", "--binary", "--", tree) or "").encode())
        for rel in sorted(p for p in changed if not _git(top, "ls-files", "--error-unmatch", "--", p)):
            f = Path(top) / rel
            if f.is_file():
                h.update(b"\0untracked\0" + rel.encode() + b"\0" + f.read_bytes())
        out["code_dirty_sha256"] = h.hexdigest()
        out["code_dirty_files"] = sorted(changed)
    return out


def _training_tree(code_dir: Path, top: str) -> str:
    """The training folder `code_dir` sits in, relative to the repo: training/ in the app repo, docs/training/ here."""
    for parent in [code_dir, *code_dir.parents]:
        if parent.name == "training":
            try:
                return parent.relative_to(Path(top).resolve()).as_posix()
            except ValueError:
                break
    return "training"


def require_clean(provenance: dict) -> None:
    """Stop a build whose training code has uncommitted changes, so every dataset names a commit it matches.

    ASK_LORA_ALLOW_DIRTY=1 lets a local experiment through; its stats still record code_dirty and the changed files."""
    code_info = provenance.get("code", provenance)
    if code_info.get("code_dirty") and os.environ.get("ASK_LORA_ALLOW_DIRTY") != "1":
        files = ", ".join(code_info.get("code_dirty_files", [])[:8])
        raise SystemExit(f"refusing to build from uncommitted training code ({files}); commit it, "
                         "or set ASK_LORA_ALLOW_DIRTY=1 for a local experiment that will not be trained on")


def _pin_commit(pin: Path, top: str) -> str | None:
    tail = pin.name.rsplit("-", 1)[-1]
    return (_git(top, "rev-parse", "--verify", "--quiet", f"{tail}^{{commit}}") or "").strip() or None


def service(harbor_repo, code_dir) -> dict:
    harbor = Path(harbor_repo).resolve()
    top = (_git(code_dir, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return {"service_head": "unknown", "service_source": "no git"}
    if harbor == Path(top).resolve():
        head = (_git(top, "log", "-1", "--format=%H", "--", "service") or "").strip() or "unknown"
        dirty = bool((_git(top, "status", "--porcelain=v1", "--", "service") or "").strip())
        return {"service_head": head, "service_dirty": dirty, "service_source": "repo"}
    files = sorted(str(p.relative_to(harbor)) for p in harbor.rglob("*") if p.is_file())
    blobs = (_git(top, "hash-object", "--stdin-paths", stdin="".join(str(harbor / f) + "\n" for f in files)) or "").split()
    have = dict(zip(files, blobs, strict=True))
    tree = hashlib.sha256("".join(f"{have[f]} {f}\n" for f in files).encode()).hexdigest()
    out = {"service_head": None, "service_source": f"pin {os.path.relpath(harbor, top)}", "pin_files": len(files), "pin_tree_sha256": tree}
    commit = _pin_commit(harbor, top)
    if not commit:
        return out | {"pin_commit": None}
    want = {}
    for ln in (_git(top, "ls-tree", "-r", commit, "--", "service", "scripts") or "").splitlines():
        meta, path = ln.split("\t", 1)
        want[path] = meta.split()[2]
    differ = sorted(p for p in set(have) | set(want) if have.get(p) != want.get(p))
    service_ok = not [p for p in differ if p.startswith("service/")]
    out |= {"pin_commit": commit, "service_head": commit if service_ok else None, "service_matches_pin_commit": service_ok,
            "pin_differs_from_commit": [{"path": p, "pin_blob": have.get(p), "commit_blob": want.get(p)} for p in differ]}
    return out


def describe(code_dir, harbor_repo=None) -> dict:
    out = code(code_dir)
    if harbor_repo:
        out |= service(harbor_repo, code_dir)
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(describe(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
