#!/bin/bash
# Synthetic presentation contract. Never launches Harbor or contacts a service.
set -euo pipefail
project_root=$(cd "$(dirname "$0")/.." && pwd)
brief_time_scratch=$(mktemp -d "${TMPDIR:-/tmp}/harbor-brief-time.XXXXXX")
trap 'rm -rf "$brief_time_scratch"' EXIT
swiftc -parse-as-library -swift-version 5 \
    -module-cache-path "$brief_time_scratch/module-cache" \
    "$project_root/app/Sources/HarborApp/NotchDay.swift" \
    "$project_root/app/Sources/HarborApp/BriefSummary.swift" \
    "$project_root/app/Sources/HarborApp/BriefTimeContext.swift" \
    "$project_root/tests/BriefTimeChecks.swift" \
    -o "$brief_time_scratch/brief-time-checks"
"$brief_time_scratch/brief-time-checks"
