#!/bin/bash
# Build the staged Swift sources (scripts/intel/stage.sh) for x86_64, in release mode.
#
#   scripts/intel/swift.sh            # prints the products folder on success
#
# STAGE and SCRATCH default to .harbor-build/swift-source-intel and .harbor-build/swift-x86.
# JOBS (default 2) limits compiler parallelism; a Swift release build is heavy, so run one at a time.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
STAGE=${STAGE:-$ROOT/.harbor-build/swift-source-intel}
SCRATCH=${SCRATCH:-$ROOT/.harbor-build/swift-x86}
args=(--package-path "$STAGE" --scratch-path "$SCRATCH" --disable-sandbox -c release --arch x86_64)
swift build "${args[@]}" --jobs "${JOBS:-2}"
rc=$?
echo "swift build exit $rc"
[ $rc -eq 0 ] && swift build "${args[@]}" --show-bin-path
exit $rc
