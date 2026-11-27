#!/bin/bash
# Shorter r5 pick (user via coordinator): base vs step-01800 and step-02126 only. Reuses the finished mmeval
# (pick/mmeval, which also holds step-01080; pick only reads the checkpoints in the text results).
# The text pass is cut from 600 to 240 generations per model: 240 dev steps (60 safety x 1, 60 absence x 1, the rest x 1).
set -u
HARBOR=$HOME/harbor; PY=$HARBOR/venv/bin/python; TD=$HARBOR/gemma; OUT=$TD/ask-lora-out; MM=$HARBOR/mmeval; PK=$TD/pick
CK=step-01800,step-02126
BASE="$(cat "$TD/base_path")"
RUN_ID=$(date +%Y%m%dT%H%M%S)
say() { echo "== $(date '+%F %T %Z') $*"; }
die() { say "FAIL: $*"; exit 1; }
[ -f "$PK/mmeval/results.json" ] || die "no finished mmeval to reuse"
mkdir -p "$PK/text"
say "short pick: base + $CK, run $RUN_ID, 240 steps, 1 sample each"
for name in base ${CK//,/ }; do
  D="$PK/text-$name"; rm -rf "$D"; mkdir -p "$D"
  if [ "$name" = base ]; then ARGS=(--ckpts base); else ARGS=(--ckpts "$name" --no-base); fi
  say "text $name"
  CUDA_VISIBLE_DEVICES=0 "$PY" "$MM/pick_text.py" generate --base "$BASE" --ckpt-root "$OUT" "${ARGS[@]}" --run-id "$RUN_ID" \
      --data "$MM/data/text-dev.jsonl" --out "$D" --limit 240 --samples 1 --samples-safety 1 --samples-absence 1 \
      --safety-steps 60 --absence-steps 60 --batch 8 --device 0 > "$PK/text-$name.log" 2>&1 || die "text $name (tail $PK/text-$name.log)"
done
cat "$PK"/text-base/text_answers.jsonl "$PK"/text-step-01800/text_answers.jsonl "$PK"/text-step-02126/text_answers.jsonl > "$PK/text/text_answers.jsonl"
"$PY" - "$PK" "$CK" <<'PYI'
import json, sys
from pathlib import Path
pk, ck = Path(sys.argv[1]), sys.argv[2].split(",")
info = json.loads((pk / "text-base" / "text_run_info.json").read_text())
info["checkpoints"] = ["base"] + ck
(pk / "text" / "text_run_info.json").write_text(json.dumps(info, indent=1))
PYI
"$PY" "$MM/pick_text.py" score --data "$MM/data/text-dev.jsonl" --out "$PK/text" --ckpt-root "$OUT" --limit 240 > "$PK/score.log" 2>&1 || die "score failed"
TOLS=(); for t in audio_wer_norm=0.03 audio_cer=0.02 audio_punct=0.05 audio_serving_wer_norm=0.03; do TOLS+=(--tol "$t"); done
"$PY" "$MM/pick_text.py" pick --text-results "$PK/text/text_results.json" --mmeval-results "$PK/mmeval/results.json" \
    --ckpt-root "$OUT" --strict --require-reading --top 2 "${TOLS[@]}" --json-out "$PK/picked.json" > "$PK/picked.path" 2> "$PK/picked.table.md"; rc=$?
cat "$PK/picked.table.md"
[ $rc -eq 0 ] && { say "TOP:"; cat "$PK/picked.path"; } || say "PICK rc $rc (3 = incomplete evidence; other = no checkpoint qualifies)"
exit $rc
