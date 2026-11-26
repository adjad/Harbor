#!/bin/bash
# Synthetic Brief render timing: open-to-content and card-section re-renders.
# Invented fixture data; never starts an AppDelegate, a service or the network.
# A heavy compile: run it under harbor-heavy.lock.
set -euo pipefail
source "$(dirname "$0")/swiftc_nested_sandbox.sh"
project_root=$(cd "$(dirname "$0")/.." && pwd)
scratch=$(mktemp -d "/private/tmp/harbor-brief-bench.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
export HARBOR_HOME="$scratch/state"
export TMPDIR="$scratch/tmp"
mkdir -p "$HARBOR_HOME" "$TMPDIR"
sources=()
while IFS= read -r -d '' source_file; do
    sources+=("$source_file")
done < <(find "$project_root/app/Sources/HarborApp" -type f -name '*.swift' ! -name main.swift -print0)
swiftc ${swiftc_nested_sandbox_flags[@]+"${swiftc_nested_sandbox_flags[@]}"} \
    -parse-as-library -swift-version 5 -O -D HARBOR_NOTCH_QA -j 2 \
    -target "$(uname -m)-apple-macosx14.0" \
    -module-cache-path "$scratch/module-cache" \
    "${sources[@]}" "$project_root/tests/BriefRenderBench.swift" \
    -lsqlite3 -o "$scratch/brief-bench"
real_home="$("$scratch/brief-bench" --print-real-home)"
/usr/bin/sandbox-exec -D REAL_HOME="$real_home" -p '(version 1)
    (allow default)
    (deny network*)
    (deny file-read* file-write* (subpath (param "REAL_HOME")))' \
    "$scratch/brief-bench"
