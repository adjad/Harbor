#!/bin/bash
# Harbor Ask LoRA steps (two models, one dataset):
#   ~/harbor/run.sh <gemma|bolt> <step> [args]      steps: check base unpack <sha256> preflight smoke train status sample merge package freebase
#   (train: NPROC=<n> torchrun processes, default every GPU; GLOBAL_BATCH=<rows> LR=<lr> EXTRA_TRAIN_ARGS='--audio-weight 0.08 ...')
#   ~/harbor/run.sh <gemma|bolt> sweep [file]       one training run per GPU (CUDA/HIP_VISIBLE_DEVICES=i, NPROC=1, own --out); then <tag> sweepstatus
#   ~/harbor/run.sh <gemma|bolt> auto <hours>       unattended preflight..package for one model; then <tag> autostatus
#   ~/harbor/run.sh both <hours-per-model> <sha256> gemma then bolt back to back, unattended; then bothstatus
# Each model keeps its files under ~/harbor/<tag>/ (data, out, merged, adapter zip), so nothing is overwritten.
set -uo pipefail
WORK="$(cd "$(dirname "$0")" && pwd)"
PY="$WORK/venv/bin/python"
UP="$WORK/uploads"; HF="$WORK/hf"
[ -f "$WORK/models.env" ] && . "$WORK/models.env"
DATA_ZIP=ask-lora-data.zip
TIGHT_GB="${TIGHT_GB:-80}"     # run-both deletes a finished base when free disk is under this
GEMMA_REPO="google/gemma-4-E4B-it"; GEMMA_REV="ee0ef6023621cff504d758262d4e04895a5af4a2"
die() { echo "FAIL: $*"; exit 1; }
need() { [ -e "$1" ] || die "$1 missing - run '$2' first"; }
free_gb() { df -BG --output=avail "$WORK" | tail -1 | tr -dc 0-9; }
bolt_rev_ok() { [[ "${BOLT_REV:-}" =~ ^[0-9a-f]{40}$ ]] || die "BOLT_REV is not a pinned 40-hex commit (see $WORK/models.env). Pin the exact revision of ${BOLT_REPO:-nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-BF16}, edit models.env, and retry"; }
say() { echo "== $(date '+%F %T %Z') $*"; }

cfg() {   # per-model settings
  TD="$WORK/$TAG"
  case "$TAG" in
    gemma) REPO="$GEMMA_REPO"; REV="$GEMMA_REV"; MERGED="$TD/gemma-4-E4B-it-Wisp-Ask-merged" ;;
    bolt) bolt_rev_ok; REPO="${BOLT_REPO:-nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-BF16}"; REV="$BOLT_REV"
          MERGED="$TD/NVIDIA-Nemotron-3.5-Bolt-E4B-Harbor-Ask-merged" ;;
  esac
  DATA="$TD/ask-lora"; OUT="$TD/ask-lora-out"
  mkdir -p "$TD"
}
base() { [ -s "$TD/base_path" ] || die "$TAG base not downloaded (run: $TAG base)"; cat "$TD/base_path"; }
T() { "$PY" "$DATA/train.py" "$@" --tag "$TAG"; }
# GPU helpers: GPU_VENDOR (models.env) picks amd-smi / rocm-smi on AMD ROCm, nvidia-smi otherwise.
is_amd() { [ "${GPU_VENDOR:-}" = amd ]; }
gpu_count() {
  if is_amd; then
    local n=0
    command -v amd-smi >/dev/null 2>&1 && n=$(amd-smi list 2>/dev/null | grep -c '^GPU' || true)
    if [ "${n:-0}" -eq 0 ] && command -v rocm-smi >/dev/null 2>&1; then
      n=$(rocm-smi --showid 2>/dev/null | grep -o '^GPU\[[0-9]*\]' | sort -u | wc -l | tr -d ' ')
    fi
    [ "${n:-0}" -gt 0 ] || n=$("$PY" -c "import torch; print(torch.cuda.device_count())" 2>/dev/null || echo 0)
    echo "${n:-0}"
  else
    nvidia-smi -L 2>/dev/null | grep -c '^GPU' || true
  fi
}
gpu_info() {   # one line per GPU kind for `check`
  if is_amd; then
    { amd-smi static --asic --vram 2>/dev/null || rocm-smi --showproductname --showmeminfo vram 2>/dev/null; } \
      | grep -iE 'market_name|card series|target_graphics|size|vram total memory' | sort | uniq -c | head -12
    echo "arch: $(rocminfo 2>/dev/null | grep -oE -m1 'gfx[0-9a-f]+' | head -1)"
  else
    nvidia-smi --query-gpu=name,memory.used,memory.total,compute_cap,driver_version --format=csv,noheader
  fi
}
gpu_mem_used_mib() {   # one number per GPU per call, for the smoke monitor
  if is_amd; then
    local m
    m=$(rocm-smi --showmeminfo vram 2>/dev/null | awk '/VRAM Total Used Memory/ {printf "%d\n", $NF / 1048576}')
    [ -n "$m" ] || m=$(amd-smi metric -m 2>/dev/null | awk '/USED_VRAM/ {print $2}')
    [ -z "$m" ] || echo "$m"
  else
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits
  fi
}
gpu_status() {   # utilization and memory for `status`
  if is_amd; then
    rocm-smi --showuse --showmeminfo vram 2>/dev/null | grep -E 'GPU use|VRAM Total (Used )?Memory' \
      || amd-smi metric -u -m 2>/dev/null | grep -E '^GPU|GFX_ACTIVITY|USED_VRAM|TOTAL_VRAM'
  else
    nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader
  fi
}
# The training launcher: torchrun (data parallel, NPROC processes) on a multi-GPU node, plain python on one GPU.
# Everything else (preflight, smoke, merge, sample) stays single-process on GPU 0.
launch_train() {
  local n="${NPROC:-$(gpu_count)}"
  if [ "${n:-1}" -gt 1 ]; then
    "$WORK/venv/bin/torchrun" --standalone --nproc_per_node="$n" "$DATA/train.py" "$@"
  else
    "$PY" "$DATA/train.py" "$@"
  fi
}
TRAIN_EXTRA=( ${GLOBAL_BATCH:+--global-batch "$GLOBAL_BATCH"} ${LR:+--lr "$LR"} ${EXTRA_TRAIN_ARGS:-} )
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True PYTORCH_HIP_ALLOC_CONF=expandable_segments:True

TAG="${1:-}"
case "$TAG" in
  gemma|bolt) shift; STEP="${1:-help}"; cfg ;;
  both|bothstatus|_both) STEP="$TAG"; TAG="" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac

case "$STEP" in
check)
  echo "model: $TAG  repo: $REPO @ $REV"
  [ -f "$WORK/SETUP_DONE" ] && echo "setup: done" || echo "setup: NOT finished (tail ~/harbor/setup.log)"
  echo "vendor: ${GPU_VENDOR:-nvidia}"; gpu_info
  echo "gpus: $(gpu_count)  (train launches torchrun with NPROC=${NPROC:-$(gpu_count)})"
  "$PY" -c "import torch,torchvision,transformers,peft;print('torch',torch.__version__,'torchvision',torchvision.__version__,'cuda',torch.version.cuda,'hip',torch.version.hip,'cuda ok',torch.cuda.is_available(),'arches',torch.cuda.get_arch_list(),'transformers',transformers.__version__,'peft',peft.__version__)"
  "$PY" -c "import transformers as t;print('multimodal class:', 'AutoModelForMultimodalLM' if hasattr(t,'AutoModelForMultimodalLM') else 'AutoModelForImageTextToText' if hasattr(t,'AutoModelForImageTextToText') else 'NONE')"
  [ -s "$TD/base_path" ] && echo "base: $(cat "$TD/base_path")" || echo "base: not downloaded (run: $TAG base)"
  [ -f "$UP/$DATA_ZIP" ] && echo "data zip: $(sha256sum "$UP/$DATA_ZIP" | cut -c1-64)  (compare with brev/upload-zip.json)" || echo "data zip: MISSING - upload $DATA_ZIP into ~/harbor/uploads"
  df -h "$WORK" | tail -1 ;;
base)
  if [ -s "$TD/base_path" ] && [ -d "$(cat "$TD/base_path")" ]; then echo "base already at $(cat "$TD/base_path")"; exit 0; fi
  [ "$(free_gb)" -ge 25 ] || die "under 25GB free; free space (or run '$TAG freebase' on a finished model) first"
  HF_HUB_ENABLE_HF_TRANSFER=1 "$PY" - "$REPO" "$REV" "$HF/$TAG" "$TD/base_path" <<'PY' || die "$TAG base download failed"
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
repo, rev, cache, dest = sys.argv[1:5]
try:
    p = snapshot_download(repo, revision=rev, cache_dir=cache,
                          allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt", "*.model"])
except Exception as exc:  # noqa: BLE001
    print(f"download failed: {type(exc).__name__}: {exc}")
    if any(w in str(exc).lower() for w in ("401", "403", "gated", "restricted", "token", "access")):
        print(f"{repo} may be gated or need a licence click-through: accept the terms on its Hugging Face page,"
              " export HF_TOKEN, and retry. Read the licence before training and sharing a derivative.")
    sys.exit(1)
if Path(p).name != rev:
    sys.exit(f"snapshot {Path(p).name} is not the pinned revision {rev}")
Path(dest).write_text(p)
print("base at", p)
PY
  ;;
unpack)
  # The sha256 comes from brev/upload-zip.json on the Mac, never from the archive itself.
  [ -f "$UP/$DATA_ZIP" ] || die "upload $DATA_ZIP into ~/harbor/uploads first"
  [ -n "${2:-}" ] || die "usage: $TAG unpack <sha256 from brev/upload-zip.json>"
  "$PY" - "$UP/$DATA_ZIP" "$DATA" "$2" <<'PY' || die "archive check failed; nothing unpacked"
import hashlib, json, shutil, sys, tempfile, zipfile
from pathlib import Path
archive, dest, want = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].lower()
names = {"train.jsonl", "dev.jsonl", "train.py", "render.py", "parity.jsonl", "stats.json", "MANIFEST.json"}
optional = {"vision_train.jsonl", "vision_dev.jsonl", "audio_train.jsonl", "audio_dev.jsonl"}
if hashlib.sha256(archive.read_bytes()).hexdigest() != want:
    sys.exit("archive sha256 does not match the one from upload-zip.json")
with zipfile.ZipFile(archive) as z:
    # r3: optional vision rows with their images under vision/, optional audio rows with their 16 kHz WAV clips
    # under audio/ (safe names only, any depth, no "..").
    import re
    got = {i.filename for i in z.infolist()}
    extra = got - names - optional
    bad = [n for n in extra if ".." in n or not (
        re.fullmatch(r"vision/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:png|jpe?g|webp)", n)
        or re.fullmatch(r"audio/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.wav", n))]
    if not names <= got or bad or any(i.is_dir() for i in z.infolist()):
        sys.exit("unexpected archive contents")
    files = json.loads(z.read("MANIFEST.json"))["files"]
    if set(files) != got - {"MANIFEST.json"}:
        sys.exit("MANIFEST does not list exactly the archive's files")
    for n, h in files.items():
        if hashlib.sha256(z.read(n)).hexdigest() != h:
            sys.exit(f"{n}: sha256 mismatch")
    stage = Path(tempfile.mkdtemp(prefix=".ask-lora-", dir=dest.parent))
    z.extractall(stage)
shutil.rmtree(dest, ignore_errors=True)
stage.rename(dest)
print(f"unpacked {len(files)} verified files -> {dest}")
PY
  ;;
preflight) need "$DATA/train.jsonl" "$TAG unpack"; T preflight --data "$DATA" --base "$(base)" ;;
smoke)
  need "$DATA/train.jsonl" "$TAG unpack"
  ( while sleep 2; do gpu_mem_used_mib; done ) > "$DATA/smoke-mem.txt" & M=$!
  CUDA_VISIBLE_DEVICES=0 HIP_VISIBLE_DEVICES=0 T smoke --data "$DATA" --base "$(base)" --out "$OUT" ${2:+--max-len "$2"} ${QLORA:+--qlora} "${TRAIN_EXTRA[@]}" 2>&1 | tee "$DATA/smoke.log"
  rc=${PIPESTATUS[0]}; kill $M 2>/dev/null
  echo "$(is_amd && echo amd-smi || echo nvidia-smi) peak: $(sort -n "$DATA/smoke-mem.txt" | tail -1) MiB"; exit "$rc" ;;
train)
  need "$DATA/train.jsonl" "$TAG unpack"
  if [ -f "$DATA/train.pid" ] && kill -0 "$(cat "$DATA/train.pid")" 2>/dev/null; then die "already running (pid $(cat "$DATA/train.pid"))"; fi
  nohup "$0" "$TAG" _train "${2:-}" "${3:-}" > "$DATA/train.out" 2>&1 & echo $! > "$DATA/train.pid"
  echo "training started (pid $!, ${NPROC:-$(gpu_count)} GPU process(es)). Check with: ~/harbor/run.sh $TAG status; adapters every --save-every steps land in $OUT/ckpt/step-NNNNN" ;;
_train)
  launch_train train --tag "$TAG" --data "$DATA" --base "$(base)" --out "$OUT" ${2:+--resume "$2"} ${3:+--max-len "$3"} \
    ${QLORA:+--qlora} "${TRAIN_EXTRA[@]}" ;;
sweep)
  # One independent single-GPU run per config line, so a B300 node trains up to 8 configs at once. A config file has
  # one run per line: "<name> <train.py flags>", e.g. "lr1e4 --lr 1e-4 --anchor-kl 0.5". Without a file the default
  # grid below runs (8 lines = 8 GPUs). Every run gets its own pid, log, and OUT=$TD/sweep/<name> (so ckpt/ and best/ never
  # collide); the multimodal GPU eval then picks the winner from the sweep/*/ckpt dirs.
  need "$DATA/train.jsonl" "$TAG unpack"
  CFG="${2:-$TD/sweep.txt}"
  if [ ! -f "$CFG" ]; then
    CFG="$TD/sweep.txt"
    cat > "$CFG" <<'GRID'
a_lr1e4_k05 --lr 1e-4 --epochs 2 --anchor-kl 0.5
b_lr2e4_k05 --lr 2e-4 --epochs 2 --anchor-kl 0.5
c_lr1e4_k10 --lr 1e-4 --epochs 2 --anchor-kl 1.0
d_lr2e4_k10 --lr 2e-4 --epochs 2 --anchor-kl 1.0
e_lr1e4_k05_e3 --lr 1e-4 --epochs 3 --anchor-kl 0.5
f_lr2e4_k20 --lr 2e-4 --epochs 2 --anchor-kl 2.0
g_lr1e4_k05_text --lr 1e-4 --epochs 2 --anchor-kl 0.5 --anchor-text-frac 0.1
h_lr5e5_k05_e3 --lr 5e-5 --epochs 3 --anchor-kl 0.5
GRID
    echo "no config file given: wrote the default 8-run grid to $CFG"
  fi
  NG=$(gpu_count); mkdir -p "$TD/sweep"; i=0
  while IFS= read -r line; do
    [ -z "${line// }" ] && continue
    case "$line" in \#*) continue ;; esac
    name="${line%% *}"; args="${line#"$name"}"
    [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "bad sweep run name '$name'"
    [ "$i" -lt "${NG:-1}" ] || die "config $((i + 1)) ('$name') has no GPU: this node lists ${NG:-0}; trim $CFG"
    R="$TD/sweep/$name"; mkdir -p "$R"
    # train.py reads <out>/smoke.json (the smoke gate), and a sweep run's --out is $R/out
    mkdir -p "$R/out"; if [ -f "$OUT/smoke.json" ]; then cp "$OUT/smoke.json" "$R/out/"; fi
    if [ -f "$R/pid" ] && kill -0 "$(cat "$R/pid")" 2>/dev/null; then die "sweep run $name already running (pid $(cat "$R/pid"))"; fi
    # shellcheck disable=SC2086
    CUDA_VISIBLE_DEVICES=$i HIP_VISIBLE_DEVICES=$i nohup "$PY" "$DATA/train.py" train --tag "$TAG" --data "$DATA" --base "$(base)" --out "$R/out" \
      ${QLORA:+--qlora} "${TRAIN_EXTRA[@]}" $args > "$R/train.out" 2>&1 & echo $! > "$R/pid"
    echo "gpu $i: $name (pid $!)  flags:$args"
    i=$((i + 1))
  done < "$CFG"
  echo "$i sweep run(s) started. Check with: ~/harbor/run.sh $TAG sweepstatus" ;;
sweepstatus)
  for R in "$TD"/sweep/*/; do
    [ -d "$R" ] || continue
    n=$(basename "$R"); st="not running"
    [ -f "$R/pid" ] && kill -0 "$(cat "$R/pid")" 2>/dev/null && st="RUNNING pid $(cat "$R/pid")"
    echo "== $n: $st"
    [ -f "$R/out/train.log.jsonl" ] && { grep '"event": "eval"' "$R/out/train.log.jsonl" | tail -1; }
    [ -f "$R/train.out" ] && grep -E "Error|FAIL|OOM|Traceback|REFUSING" "$R/train.out" | tail -2
  done ;;
status)
  if [ -f "$DATA/train.pid" ] && kill -0 "$(cat "$DATA/train.pid")" 2>/dev/null; then echo "RUNNING pid $(cat "$DATA/train.pid")"; else echo "not running"; fi
  gpu_status
  [ -f "$OUT/train.log.jsonl" ] && { grep '"event": "eval"' "$OUT/train.log.jsonl" | tail -4; tail -1 "$OUT/train.log.jsonl"; }
  [ -f "$DATA/train.out" ] && grep -E "Error|FAIL|OOM|Traceback|REFUSING" "$DATA/train.out" | tail -5 ;;
sample) need "$OUT/best/adapter_model.safetensors" "$TAG train"; T sample --base "$(base)" --adapter "$OUT/${2:-best}" --data "$DATA" ;;
merge)
  need "$OUT/best/adapter_model.safetensors" "$TAG train"
  rm -rf "$MERGED"
  T merge --base "$(base)" --adapter "$OUT/${2:-best}" --data "$DATA" --out "$MERGED" ;;
package)
  need "$OUT/best/adapter_model.safetensors" "$TAG train"
  "$PY" - "$OUT" "$DATA" "$TD/ask-lora-adapter.zip" <<'PY' || die "packaging the adapter failed"
import sys, zipfile
from pathlib import Path
from safetensors import safe_open
out, data, dest = map(Path, sys.argv[1:4])
best = out / "best" / "adapter_model.safetensors"
with safe_open(str(best), "pt") as fh:
    empty = [k for k in fh.keys() if 0 in fh.get_slice(k).get_shape()]
    n = len(list(fh.keys()))
if empty or not n:
    sys.exit(f"adapter has {len(empty)} empty tensors of {n} - not packaging")
files = [f for d in ("best", "final") for f in sorted((out / d).glob("*")) if f.name != "trainer_state.pt"]
files += [p for p in (out / "train.log.jsonl", out / "smoke.json", data / "smoke.log", data / "train.out") if p.exists()]
with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(f, f.relative_to(out) if f.is_relative_to(out) else f.name)
print(f"{dest}: {len(files)} files, {n} adapter tensors, {dest.stat().st_size / 1e6:.1f} MB")
PY
  # provenance for the Mac-side quantisation and the before/after comparison
  "$PY" - "$TAG" "$REPO" "$REV" "$(cat "$TD/base_path" 2>/dev/null)" "$UP/$DATA_ZIP" "$TD/run-info.json" <<'PY' || echo "WARN: run-info.json not written"
import hashlib, json, sys, time
from pathlib import Path
import peft, torch, transformers
tag, repo, rev, base, zp, dest = sys.argv[1:7]
tpl = Path(base) / "chat_template.jinja"
info = {"tag": tag, "base_repo": repo, "base_rev": rev, "written": time.strftime("%FT%TZ", time.gmtime()),
        "base_template_sha256": hashlib.sha256(tpl.read_bytes()).hexdigest() if tpl.is_file() else None,
        "data_zip_sha256": hashlib.sha256(Path(zp).read_bytes()).hexdigest() if Path(zp).is_file() else None,
        "torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__}
Path(dest).write_text(json.dumps(info, indent=1) + "\n")
print(json.dumps(info))
PY
  sha256sum "$TD/ask-lora-adapter.zip"
  [ -d "$MERGED" ] && { du -sh "$MERGED"; (cd "$MERGED" && sha256sum *.safetensors > SHA256SUMS && cat SHA256SUMS); }
  echo "download $TD/ask-lora-adapter.zip, $TD/run-info.json and the folder $MERGED (for quantizing on the Mac)" ;;
freebase)
  # Delete this model's bf16 base download once its merge is packaged (only when disk is tight).
  need "$TD/ask-lora-adapter.zip" "$TAG package"; need "$MERGED/SHA256SUMS" "$TAG package"
  rm -rf "$HF/$TAG" "$TD/base_path"
  echo "$TAG base deleted; free disk now $(free_gb)GB (re-download with: $TAG base)" ;;
auto)
  need "$DATA/train.jsonl" "$TAG unpack <sha256>"
  if [ -f "$TD/auto.pid" ] && kill -0 "$(cat "$TD/auto.pid")" 2>/dev/null; then die "auto already running (pid $(cat "$TD/auto.pid"))"; fi
  nohup setsid "$0" "$TAG" _auto "${2:-3}" > "$TD/auto.log" 2>&1 < /dev/null & echo $! > "$TD/auto.pid"
  sleep 2; echo "unattended run started (pid $(cat "$TD/auto.pid")); check with: ~/harbor/run.sh $TAG autostatus" ;;
_auto)
  END=$("$PY" -c "import time,sys; print(int(time.time() + float(sys.argv[1]) * 3600))" "${2:-3}")
  say "[$TAG] deadline $(date -d "@$END" '+%F %T %Z')"
  "$0" "$TAG" preflight || { say "STOP: [$TAG] preflight failed"; exit 1; }
  ML=""
  for try in 10240 8192; do
    say "[$TAG] smoke --max-len $try"
    "$0" "$TAG" smoke "$try"; rc=$?
    [ "$rc" -eq 0 ] && { ML=$try; break; }
    [ "$rc" -ge 5 ] && { say "STOP: [$TAG] smoke found a pipeline problem (exit $rc) - see $DATA/smoke.log"; exit 1; }
    say "[$TAG] smoke exit $rc (3 = memory tight, 4 = OOM); trying a shorter cap"
  done
  [ -n "$ML" ] || { say "STOP: [$TAG] no length cap passed smoke"; exit 1; }
  # smoke timed ONE GPU; N data-parallel GPUs measured about 0.8 N x (row lengths vary, the slowest rank sets the pace)
  EP=$(SPEEDUP="${SPEEDUP:-$(n=${NPROC:-$(gpu_count)}; [ "${n:-1}" -gt 1 ] && echo "$((n * 8 / 10))" || echo 1)}" "$PY" - "$OUT/smoke.json" "$END" <<'PY'
import json, os, sys, time
s = json.load(open(sys.argv[1])); end = float(sys.argv[2])
per = (s["epoch_min"] + s["eval_min_per_epoch"]) * 1.15 / max(1.0, float(os.environ.get("SPEEDUP") or 1))
print(max(1, min(2, int(((end - time.time()) / 60 - 30) // per))))
PY
)
  STOP=$(( END - 30 * 60 ))
  say "[$TAG] training $EP epoch(s) at --max-len $ML; hard stop $(date -d "@$STOP" '+%T %Z')"
  launch_train train --tag "$TAG" --data "$DATA" --base "$(base)" --out "$OUT" --epochs "$EP" --max-len "$ML" \
    --stop-at "$STOP" ${QLORA:+--qlora} "${TRAIN_EXTRA[@]}" >> "$DATA/train.out" 2>&1 || { say "STOP: [$TAG] training failed - tail of train.out:"; tail -5 "$DATA/train.out"; exit 1; }
  say "[$TAG] training done: $(grep -E '"event": "(done|stopped_at_deadline)"' "$OUT/train.log.jsonl" | tail -1)"
  "$0" "$TAG" sample > "$TD/sample.txt" 2>&1 || say "[$TAG] sample failed (non-fatal)"
  "$0" "$TAG" merge || { say "STOP: [$TAG] merge failed (adapter is in $OUT/best)"; exit 1; }
  "$0" "$TAG" package
  say "[$TAG] ALL DONE - download $TD/ask-lora-adapter.zip and $MERGED" ;;
autostatus)
  if [ -f "$TD/auto.pid" ] && kill -0 "$(cat "$TD/auto.pid")" 2>/dev/null; then echo "AUTO RUNNING (pid $(cat "$TD/auto.pid"))"; else echo "auto not running"; fi
  [ -f "$TD/auto.log" ] && grep '^== ' "$TD/auto.log" | tail -12
  "$0" "$TAG" status 2>/dev/null | tail -6 ;;
both)
  # gemma, then bolt, each with its own deadline of <hours-per-model>. Stops at the first failure
  # (GPU hours are billed): fix it, then resume that model's steps by hand or re-run both.
  H="${2:-3}"; SHA="${3:-}"
  [ -n "$SHA" ] || die "usage: both <hours-per-model> <sha256 from brev/upload-zip.json>"
  bolt_rev_ok
  [ -f "$UP/$DATA_ZIP" ] || die "upload $DATA_ZIP into ~/harbor/uploads first"
  if [ -f "$WORK/both.pid" ] && kill -0 "$(cat "$WORK/both.pid")" 2>/dev/null; then die "both already running (pid $(cat "$WORK/both.pid"))"; fi
  nohup setsid "$0" _both "$H" "$SHA" > "$WORK/both.log" 2>&1 < /dev/null & echo $! > "$WORK/both.pid"
  sleep 2; echo "run-both started (pid $(cat "$WORK/both.pid")); check with: ~/harbor/run.sh bothstatus" ;;
_both)
  H="${2:-3}"; SHA="$3"
  for t in gemma bolt; do
    say "=== $t: base, unpack, auto ($H h)"
    "$0" "$t" base || { say "STOP: $t base download failed; nothing later was started"; exit 1; }
    "$0" "$t" unpack "$SHA" || { say "STOP: $t unpack failed"; exit 1; }
    "$0" "$t" _auto "$H" || { say "STOP: $t failed; the other model was not started"; exit 1; }
    if [ "$t" = gemma ] && [ "$(free_gb)" -lt "$TIGHT_GB" ]; then
      say "free disk $(free_gb)GB < ${TIGHT_GB}GB: deleting the $t base before the next download"
      "$0" "$t" freebase || { say "STOP: could not free $t base"; exit 1; }
    fi
  done
  say "BOTH DONE - download ~/harbor/gemma and ~/harbor/bolt (adapter zip, run-info.json, merged folder), then stop the instance" ;;
bothstatus)
  if [ -f "$WORK/both.pid" ] && kill -0 "$(cat "$WORK/both.pid")" 2>/dev/null; then echo "BOTH RUNNING (pid $(cat "$WORK/both.pid"))"; else echo "both not running"; fi
  [ -f "$WORK/both.log" ] && grep '^== ' "$WORK/both.log" | tail -12
  for t in gemma bolt; do [ -d "$WORK/$t/ask-lora-out" ] && { echo "-- $t"; "$0" "$t" status 2>/dev/null | tail -4; }; done ;;
*) sed -n '2,8p' "$0" ;;
esac
