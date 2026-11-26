#!/bin/bash
# Synthetic native UI/model contract. Never launches Harbor or contacts a service.
set -euo pipefail
source "$(dirname "$0")/swiftc_nested_sandbox.sh"
project_root=$(cd "$(dirname "$0")/.." && pwd)
today_scratch=$(mktemp -d "${TMPDIR:-/tmp}/harbor-today-contract.XXXXXX")
trap 'rm -rf "$today_scratch"' EXIT
export HARBOR_HOME="$today_scratch/state"
export HARBOR_TODAY_SCRATCH="$today_scratch"
swiftc ${swiftc_nested_sandbox_flags[@]+"${swiftc_nested_sandbox_flags[@]}"} -parse-as-library -swift-version 5 \
    -module-cache-path "$today_scratch/module-cache" \
    "$project_root/app/Sources/HarborApp/DiagnosticReport.swift" \
    "$project_root/app/Sources/HarborApp/ReportDraftView.swift" \
    "$project_root/app/Sources/HarborApp/Theme.swift" \
    "$project_root/app/Sources/HarborApp/TodayView.swift" \
    "$project_root/app/Sources/HarborApp/DailyWorkspaceView.swift" \
    "$project_root/app/Sources/HarborApp/ProactiveModels.swift" \
    "$project_root/app/Sources/HarborApp/CaughtForYou.swift" \
    "$project_root/app/Sources/HarborApp/NotchDay.swift" \
    "$project_root/app/Sources/HarborApp/BriefSummary.swift" \
    "$project_root/tests/TodayPlanChecks.swift" \
    -o "$today_scratch/today-checks"
"$today_scratch/today-checks"
