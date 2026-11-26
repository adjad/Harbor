#!/bin/bash
# Compile real app models against an inert research transport and store fixtures.
set -euo pipefail
project_root=$(cd "$(dirname "$0")/.." && pwd)
library_scratch=$(mktemp -d "${TMPDIR:-/tmp}/harbor-library-contract.XXXXXX")
trap 'rm -rf "$library_scratch"' EXIT
export HARBOR_HOME="$library_scratch/state"
export PYTHONDONTWRITEBYTECODE=1
export CLANG_MODULE_CACHE_PATH="$library_scratch/clang-cache"
test_python=${HARBOR_TEST_PYTHON:-"$project_root/.venv/bin/python"}
if [[ ! -x "$test_python" ]]; then test_python=python3; fi
"$test_python" -B "$project_root/tests/research_library_fixtures.py" "$library_scratch/fixtures.json"
swiftc -parse-as-library -swift-version 5 \
    -module-cache-path "$library_scratch/module-cache" \
    "$project_root/app/Sources/HarborApp/HarborClient.swift" \
    "$project_root/app/Sources/HarborApp/ResearchExportName.swift" \
    "$project_root/app/Sources/HarborApp/AgentSend.swift" \
    "$project_root/app/Sources/HarborApp/ResearchModel.swift" \
    "$project_root/app/Sources/HarborApp/AppBugRules.swift" \
    "$project_root/app/Sources/HarborApp/ResearchLibraryModel.swift" \
    "$project_root/app/Sources/HarborApp/ResearchLibraryView.swift" \
    "$project_root/app/Sources/HarborApp/Theme.swift" \
    "$project_root/tests/ResearchLibraryChecks.swift" \
    -o "$library_scratch/library-checks"
"$library_scratch/library-checks" "$library_scratch/fixtures.json"
