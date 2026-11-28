"""_pick_segments has one definition, and it keeps only what the final answer used.

Run: python3 -m pytest test_pick_segments.py   (from this folder; invented data)
"""
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import thinking as T  # noqa: E402


def test_one_top_level_definition():
    tree = ast.parse((HERE / "thinking.py").read_text())
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes == []


READS = [("get_upcoming", "Dentist at 3:00 PM with Dr. Lee\nGym at 6:00 PM")]


def test_keeps_the_segment_the_final_relies_on_and_drops_the_rest():
    assert T._pick_segments(READS, "You have the dentist at 3:00 PM.", "what's today", 2) == [
        "Dentist at 3:00 PM with Dr. Lee"]


def test_keeps_nothing_when_the_final_uses_nothing():
    assert T._pick_segments(READS, "Nothing much.", "what's today", 2) == []


def test_limit_and_result_order():
    reads = [("get_upcoming", "Gym at 6:00 PM\nDentist at 3:00 PM")]
    final = "Dentist at 3:00 PM, then the gym at 6:00 PM."
    assert T._pick_segments(reads, final, "today", 2) == ["Gym at 6:00 PM", "Dentist at 3:00 PM"]
    assert len(T._pick_segments(reads, final, "today", 1)) == 1
