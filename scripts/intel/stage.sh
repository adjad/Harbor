#!/bin/bash
# Stage this checkout's Swift sources for the x86_64 (Intel) build.
# The staged BackendManager loses its developer-checkout fallback, so a tester's Mac
# can never start a backend from a source checkout instead of the app bundle.
#
#   scripts/intel/stage.sh            # stages into $STAGE (default .harbor-build/swift-source-intel)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
STAGE=${STAGE:-$ROOT/.harbor-build/swift-source-intel}
mkdir -p "$STAGE/Sources"
rsync -a --delete "$ROOT/app/Sources/" "$STAGE/Sources/"
cp "$ROOT/app/Package.swift" "$STAGE/Package.swift"
/usr/bin/python3 - "$STAGE/Sources/HarborApp/BackendManager.swift" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
block = re.compile(r'        let devRoot = URL\(fileURLWithPath: "[^"]+"\)\n'
                   r'        if FileManager\.default\.fileExists\(atPath: devRoot\.appendingPathComponent\("service/main\.py"\)\.path\) \{\n'
                   r'            return devRoot\n'
                   r'        \}\n')
s, n = block.subn("", s)
if n != 1:
    sys.exit("developer-checkout fallback not found exactly once in BackendManager.swift")
open(p, "w").write(s)
PY
if grep -q devRoot "$STAGE/Sources/HarborApp/BackendManager.swift"; then
  echo "developer-checkout fallback still present" >&2; exit 4
fi
echo "staged: $STAGE"
