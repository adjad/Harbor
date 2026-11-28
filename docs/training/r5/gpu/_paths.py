"""Put the directories holding gen.py / choose.py (r4 distill) and render.py (r3 brev) on sys.path.

In the repo they sit at ../../r4/distill and ../../r3/brev. On the GPU box upload copies of
gen.py, choose.py and render.py next to these scripts (or point HARBOR_DISTILL_DIR / HARBOR_RENDER_DIR at them).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.environ.get("HARBOR_DISTILL_DIR"), os.environ.get("HARBOR_RENDER_DIR"),
           os.path.join(HERE, "..", "..", "r4", "distill"),
           os.path.join(HERE, "..", "..", "r3", "brev")):
    if _d and os.path.isdir(_d) and os.path.abspath(_d) not in sys.path:
        sys.path.append(os.path.abspath(_d))
