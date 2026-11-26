"""Changed-only gate selection for Simulation QA (development rounds only).

``scripts/run_simulation_qa.py --changed-since REF`` uses this module to pick
the gates that cover the files changed since REF.  It is a speed aid between
builder rounds and NEVER a release gate: its report records the profile
``affected`` (the pipeline accepts only ``full``) and ``release_gate: false``.

Selection always fails safe toward running more:

* shared infrastructure (conftest, build-support, this selector, the runner
  outside its manifest lists, safety/policy modules, packaging and signing
  scripts, test dependencies) selects the full gate set;
* a deleted or renamed path selects the full gate set;
* a changed path that no gate covers selects the full gate set;
* otherwise a gate is selected when its inputs are reachable from a changed
  path through module-level Python imports, a test's own imports, an exact
  mention of the path or dotted module name anywhere, a test or shell script
  naming the file, a test or script that scans a containing folder, or a
  native compile list (the runner's own and build-support/simulation.py's).

Reachability over-approximates on purpose (a docstring mention is enough). The
one deliberate limit: an import inside a product function body is not followed,
because product code imports lazily into hubs (``service.config``) and following
those edges selects every test for almost any module. A test that reaches a
changed module only through such a lazy import is left to the full profile,
which every frozen candidate still runs.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

NOT_A_RELEASE_GATE = (
    "Changed-only Simulation QA: a development speed aid, NOT a release gate. "
    "Frozen candidates and harbor-build all still run the full profile."
)

# Any change here can alter what every gate means or how it runs.
FULL_TRIGGERS = (
    "conftest.py", "*/conftest.py",
    "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
    "requirements*.txt", "requirements*.in", "requirements*.lock",
    ".github/*",
    "build-support/*",
    "tests/build_pipeline/*",
    "scripts/qa_affected.py",
    "scripts/test_replay_failure_fixes.py",   # LEGACY_SCRIPT_TESTS: how gates run
    "scripts/swiftc_nested_sandbox.sh",
    "scripts/harbor-build", "scripts/harbor-build*",
    "scripts/*package*", "scripts/*sign*", "scripts/*release*", "scripts/*notar*",
    "service/safety/*",
    "service/agent/approver.py",
    "service/config/policy.yaml",
    "service/inference/remote_policy.py",
    "sandbox/*",
    "app/Package.swift", "app/Package.resolved",
    "app/Resources/*", "app/*.plist", "*.entitlements",
)
RUNNER = "scripts/run_simulation_qa.py"
# Only manifest entries, comments and blank lines may change in the runner
# without triggering the full gate set.
_MANIFEST_LINE = re.compile(
    r'^\s*(?:#.*|(?:"tests/[A-Za-z0-9_./-]+\.py",\s*)+(?:#.*)?)?$'
)
_MANIFEST_ENTRY = re.compile(r'"(tests/[A-Za-z0-9_./-]+\.py)"')
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_INDEXED_SUFFIXES = {".py", ".sh"}
_FOLDER_SCAN = re.compile(r"\brglob\b|\bglob\b|os\.walk|\biterdir\b|\bfind\b|\bls-files\b|\*\.[A-Za-z]+")
_BARE_IMPORT_ROOTS = {"scripts", "build-support", "tests"}


@dataclass
class Selection:
    ref: str
    ref_sha: str
    changed: list[str]
    tests: list[str]
    native_names: list[str]
    reasons: dict[str, list[str]]
    full_fallback: bool
    fallback_reasons: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    native_gates: Callable[[Path], list[tuple[str, list[str]]]] | None = None

    def report(self) -> dict:
        return {
            "release_gate": False,
            "notice": NOT_A_RELEASE_GATE,
            "changed_since": self.ref,
            "changed_since_sha": self.ref_sha,
            "changed_paths": self.changed,
            "full_fallback": self.full_fallback,
            "fallback_reasons": self.fallback_reasons,
            "unmapped_paths": self.unmapped,
            "selected_tests": self.tests,
            "selected_native": self.native_names,
            "reasons": self.reasons,
        }


def is_full_trigger(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in FULL_TRIGGERS)


def runner_change_is_manifest_only(diff_text: str) -> tuple[bool, set[str]]:
    """True when every changed runner line is a manifest entry, comment or blank."""
    entries: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        body = line[1:]
        if not _MANIFEST_LINE.match(body):
            return False, set()
        if line.startswith("+"):
            entries.update(_MANIFEST_ENTRY.findall(body))
    return True, entries


def _dotted(path: str) -> str | None:
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    parts = list(pure.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _module_names(path: str) -> set[str]:
    """Names a file can be imported under (repo root plus helper sys.path roots)."""
    names = set()
    dotted = _dotted(path)
    if dotted:
        names.add(dotted)
    pure = PurePosixPath(path)
    # Tests and scripts put scripts/, build-support/ or tests/ on sys.path and
    # import a file there by its bare name. Never shadow a standard module.
    if (pure.suffix == ".py" and len(pure.parts) == 2 and pure.parts[0] in _BARE_IMPORT_ROOTS
            and pure.stem.isidentifier() and pure.stem not in sys.stdlib_module_names):
        names.add(pure.stem)
    return names


def _keys(path: str) -> tuple[set[str], set[str], set[str]]:
    """Mentions that mean another file may use ``path``: (exact, name, folder).

    Exact keys (the repo path, the dotted module name) count wherever they
    appear. The file name counts only in tests and shell scripts, which read
    sources by name; in product modules it is mostly coincidence (every
    ``api.py``). A containing folder counts only in a test or script that also
    walks or globs folders (``rglob``, ``os.walk``, ``find``, ``*.swift``);
    a top-level folder alone counts only in such a test module.
    """
    pure = PurePosixPath(path)
    exact = {path}
    dotted = _dotted(path)
    if dotted:
        exact.add(dotted)
    name = {pure.name} if pure.name != "__init__.py" else set()
    folders = {str(parent) for parent in pure.parents if str(parent) != "."}
    return exact, name, folders


def _reads_by_name(path: str) -> bool:
    return path.startswith("tests/") or path.endswith(".sh")


def _is_test_module(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.parts[0] == "tests" and pure.name.startswith("test_") and pure.suffix == ".py"


def _lazy_import_counts(user: str) -> bool:
    """An import inside a function counts only when a test or test helper makes it.

    Product modules import lazily into hubs (``service.config`` imports Brief
    code inside a function), so following those edges reaches every test from
    almost any module. A test's own in-function import is a direct use.
    """
    return user.startswith("tests/")


def _docstring_ids(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                found.add(id(body[0].value))
    return found


def _tokens(text: str) -> set[str]:
    found: set[str] = set()
    for token in _TOKEN.findall(text):
        token = token.rstrip("./-")
        if not token:
            continue
        found.add(token)
        pieces = token.split("/")
        for index in range(1, len(pieces)):
            suffix = "/".join(pieces[index:])
            if suffix:
                found.add(suffix)
    return found


class RepoGraph:
    """Import and mention edges between tracked .py/.sh files."""

    def __init__(self, root: Path, tracked: list[str]):
        self.root = root
        self.tracked = set(tracked)
        self.by_name: dict[str, set[str]] = {}
        self.importers: dict[str, set[str]] = {}
        self.lazy_importers: dict[str, set[str]] = {}
        self.scans_folders: set[str] = set()
        self.mentions: dict[str, set[str]] = {}
        indexed = [path for path in tracked
                   if PurePosixPath(path).suffix in _INDEXED_SUFFIXES and (root / path).is_file()]
        for path in indexed:
            for name in _module_names(path):
                self.by_name.setdefault(name, set()).add(path)
        for path in indexed:
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _FOLDER_SCAN.search(text):
                self.scans_folders.add(path)
            if path.endswith(".py"):
                try:
                    tree = ast.parse(text)
                except SyntaxError:
                    # Unparseable source: treat every token as a possible use.
                    self._mention(path, _tokens(text))
                    continue
                # Docstrings in product code cite other files as prose ("see
                # service/proactive/acting.py"); tests' docstrings still count.
                skip = set() if _reads_by_name(path) else _docstring_ids(tree)
                strings = " ".join(node.value for node in ast.walk(tree)
                                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                                   and id(node) not in skip)
                self._mention(path, _tokens(strings))
                eager, lazy = self._imports(path, tree)
                for target in eager:
                    self.importers.setdefault(target, set()).add(path)
                for target in lazy:
                    self.lazy_importers.setdefault(target, set()).add(path)
            else:
                self._mention(path, _tokens(text))

    def _mention(self, path: str, tokens: set[str]) -> None:
        for token in tokens:
            self.mentions.setdefault(token, set()).add(path)

    def _resolve(self, name: str) -> set[str]:
        found: set[str] = set()
        parts = name.split(".")
        for index in range(1, len(parts) + 1):     # importing a.b.c runs a and a.b too
            found |= self.by_name.get(".".join(parts[:index]), set())
        return found

    def _imports(self, path: str, tree: ast.AST) -> tuple[set[str], set[str]]:
        """(module-level imports, imports inside a function body)."""
        package = (_dotted(path) or "").split(".")
        if not path.endswith("__init__.py"):
            package = package[:-1]
        eager: set[str] = set()
        lazy: set[str] = set()

        def visit(node: ast.AST, in_function: bool) -> None:
            targets = lazy if in_function else eager
            if isinstance(node, ast.Import):
                for alias in node.names:
                    targets.update(self._resolve(alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[:len(package) - node.level + 1] if node.level > 1 else package
                    module = ".".join([*base, *([node.module] if node.module else [])])
                else:
                    module = node.module or ""
                if module:
                    targets.update(self._resolve(module))
                for alias in node.names:
                    if alias.name != "*":
                        targets.update(self.by_name.get(f"{module}.{alias.name}".lstrip("."), set()))
            nested = in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            for child in ast.iter_child_nodes(node):
                visit(child, nested)

        visit(tree, False)
        eager.discard(path)
        lazy.discard(path)
        return eager, lazy - eager

    def users(self, path: str) -> set[str]:
        if _is_test_module(path) or path == RUNNER or is_full_trigger(path):
            # Leaves: nothing imports a test module, and QA infrastructure names
            # every gate's files without using them (a change to it runs everything).
            return set()
        found = set(self.importers.get(path, set()))
        found |= {user for user in self.lazy_importers.get(path, set()) if _lazy_import_counts(user)}
        exact, name, folders = _keys(path)
        for key in exact:
            found |= self.mentions.get(key, set())
        for key in name:
            found |= {user for user in self.mentions.get(key, set()) if _reads_by_name(user)}
        for key in folders:
            # A top-level folder ("service", "app") counts only in a test that
            # scans it; a script must name a deeper folder (app/Sources/HarborApp).
            found |= {user for user in self.mentions.get(key, set())
                      if user in self.scans_folders
                      and (_is_test_module(user) if "/" not in key else _reads_by_name(user))}
        found.discard(path)
        return found

    def reach(self, start: str) -> dict[str, str | None]:
        """Every file that may use ``start``, with the file it was reached from."""
        parent: dict[str, str | None] = {start: None}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for user in self.users(current):
                if user not in parent:
                    parent[user] = current
                    frontier.append(user)
        return parent


def _chain(parent: dict[str, str | None], node: str) -> str:
    steps = [node]
    while parent.get(steps[-1]) is not None and len(steps) < 6:
        steps.append(parent[steps[-1]])  # type: ignore[arg-type]
    if parent.get(steps[-1]) is not None:
        steps.append("...")
    return " <- ".join(reversed(steps[1:])) if len(steps) > 1 else node


def wrapper_gate_table(simulation_source: str) -> list[tuple[str, list[str], str]]:
    """The extra native gates build-support/simulation.py defines, read without importing it."""
    tree = ast.parse(simulation_source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
                and [getattr(elt, "id", None) for elt in node.target.elts] == ["name", "sources", "contract"]):
            table = ast.literal_eval(node.iter)
            return [(str(name), list(sources), str(contract)) for name, sources, contract in table]
    raise ValueError("build-support/simulation.py native gate table not found")


def wrapper_gates(table, build: Path, swiftc: str) -> list[tuple[str, list[str]]]:
    """The same commands build-support/simulation.py builds (fixture temp dir included)."""
    gates = []
    for name, sources, contract in table:
        binary = str(build / name)
        gates.append((f"native/{name}-compile", [
            swiftc, "-parse-as-library", "-swift-version", "5",
            "-module-cache-path", str(build / "module-cache"),
            *["app/Sources/HarborApp/" + source for source in sources], "tests/" + contract,
            "-o", binary, "build-support/FixtureTemporaryDirectory.swift"]))
        gates.append((f"native/{name}-contract", [binary]))
    return gates


def _gate_inputs(command: list[str], root: Path, tracked: set[str]) -> set[str]:
    inputs = set()
    for arg in command:
        rel = arg
        if arg.startswith(str(root) + "/"):
            rel = arg[len(str(root)) + 1:]
        if rel in tracked:
            inputs.add(rel)
    return inputs


def _untracked_changes(qa, tracked: list[str]) -> set[str]:
    """Untracked files that count as changes.

    Ignored files are already dropped (``--exclude-standard`` applies the same
    rules as ``git check-ignore``). A file under a dot-folder at the repo root
    is local tool state unless that folder already holds tracked files, so a
    new file in a tracked dot-folder still counts (fail safe toward more gates).
    """
    tracked_dot_roots = {path.split("/", 1)[0] for path in tracked
                         if path.startswith(".") and "/" in path}
    kept = set()
    for path in qa._git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        top = path.split("/", 1)[0]
        if not path or ("/" in path and top.startswith(".") and top not in tracked_dot_roots):
            continue
        kept.add(path)
    return kept


def select(qa, ref: str) -> Selection:
    """Map files changed since ``ref`` to gates; ``qa`` is the runner module."""
    root: Path = qa.ROOT
    if not ref or ref.startswith("-"):
        raise ValueError("--changed-since needs a commit-ish, not an option")
    ref_sha = qa._git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    tracked = qa._git("ls-files", "-z").split("\0")
    tracked = [path for path in tracked if path]
    changed = set(qa._git("diff", "--name-only", "--no-renames", "-z", ref_sha, "--").split("\0"))
    changed |= _untracked_changes(qa, tracked)
    changed = sorted(path for path in changed if path)

    full_tests = qa._selected_tests(["full"])        # also enforces the manifest drift check
    dependencies = dict(qa._NATIVE_GATE_DEPENDENCIES)
    table = wrapper_gate_table((root / "build-support/simulation.py").read_text(encoding="utf-8"))
    for name, _sources, _contract in table:
        dependencies[f"native/{name}-contract"] = f"native/{name}-compile"
    probe = Path("/tmp/harbor-simqa-affected-probe")
    all_native = list(qa._CANONICAL_NATIVE_GATES(probe)) + wrapper_gates(table, probe, qa.TRUSTED_SWIFTC)
    native_order = [name for name, _ in all_native]
    tracked_set = set(tracked)
    inputs = {name: _gate_inputs(command, root, tracked_set) for name, command in all_native}

    fallback: list[str] = []
    if not changed:
        fallback.append("no changed paths since the ref: nothing to map, so run everything")
    manifest_tests: set[str] = set()
    to_map: list[str] = []
    for path in changed:
        if path == RUNNER:
            diff = qa._git("diff", "--no-renames", "-U0", ref_sha, "--", RUNNER)
            only_manifest, entries = runner_change_is_manifest_only(diff)
            if only_manifest:
                manifest_tests |= entries
                continue
            fallback.append(f"{RUNNER}: runner logic changed (not only manifest lines)")
        elif is_full_trigger(path):
            fallback.append(f"{path}: shared QA, safety or packaging infrastructure")
        elif not (root / path).is_file():
            fallback.append(f"{path}: deleted or renamed")
        else:
            to_map.append(path)

    reasons: dict[str, list[str]] = {}
    unmapped: list[str] = []
    if not fallback:
        graph = RepoGraph(root, tracked)
        full_set = set(full_tests)
        for path in sorted(manifest_tests):
            if path in full_set:
                reasons.setdefault(path, []).append(f"added to the manifest in {RUNNER}")
        for path in to_map:
            parent = graph.reach(path)
            hit = False
            for test in full_tests:
                if test in parent:
                    hit = True
                    why = ("changed" if test == path else "uses " + path + (
                        " directly" if parent[test] == path else " via " + _chain(parent, test)))
                    reasons.setdefault(test, []).append(why)
            for name in native_order:
                touched = sorted(inputs[name] & parent.keys())
                if touched:
                    hit = True
                    reasons.setdefault(name, []).append(
                        f"compiles or runs {touched[0]}" + (
                            "" if touched[0] == path else f" (uses {path} via {_chain(parent, touched[0])})"))
            if not hit:
                unmapped.append(path)
        for path in unmapped:
            fallback.append(f"{path}: no gate covers it")

    if fallback:
        tests = list(full_tests)
        native_names = list(native_order)
        reasons = {"*": ["full gate set: " + item for item in fallback]}
    else:
        chosen = set(reasons)
        # Keep each compile/contract pair together so a contract never runs stale.
        for contract, compile_gate in dependencies.items():
            if contract in chosen or compile_gate in chosen:
                for name in (contract, compile_gate):
                    if name not in chosen and name in inputs:
                        reasons.setdefault(name, []).append("paired compile/contract gate")
                        chosen.add(name)
        tests = [test for test in full_tests if test in chosen]
        native_names = [name for name in native_order if name in chosen]

    selected_native = set(native_names)

    def native_gates(build: Path) -> list[tuple[str, list[str]]]:
        every = list(qa._CANONICAL_NATIVE_GATES(build)) + wrapper_gates(table, build, qa.TRUSTED_SWIFTC)
        return [(name, command) for name, command in every if name in selected_native]

    qa._NATIVE_GATE_DEPENDENCIES.update(
        {contract: dep for contract, dep in dependencies.items() if contract in selected_native})
    return Selection(ref=ref, ref_sha=ref_sha, changed=changed, tests=tests,
                     native_names=native_names, reasons=reasons, full_fallback=bool(fallback),
                     fallback_reasons=fallback, unmapped=unmapped, native_gates=native_gates)


def describe(selection: Selection) -> list[str]:
    lines = [NOT_A_RELEASE_GATE,
             f"Changed since {selection.ref} ({selection.ref_sha[:12]}): {len(selection.changed)} paths"]
    if selection.full_fallback:
        lines.append(f"Running the FULL gate set ({len(selection.tests)} modules + "
                     f"{len(selection.native_names)} native gates) because:")
        lines.extend("  " + reason for reason in selection.fallback_reasons)
        return lines
    lines.append(f"Selected {len(selection.tests)} modules + {len(selection.native_names)} native gates:")
    for name in [*selection.tests, *selection.native_names]:
        lines.append(f"  {name}: " + "; ".join(selection.reasons.get(name, [])[:3]))
    return lines


def slowest(results, count: int = 10) -> list[str]:
    ranked = sorted(results, key=lambda item: item.duration_s, reverse=True)[:count]
    return [f"  {item.duration_s:8.2f}s  {item.name}" for item in ranked]
