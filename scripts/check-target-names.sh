#!/bin/bash
# Fails when two targets in project.yml have names, or Swift module names,
# that differ only by case. Xcode keeps each target's intermediate files in
# <target>.build/ and names them after the module, and CI's disk ignores case:
# a target `pixelswitch` beside the app `PixelSwitch` shared one folder there,
# and the tool compiled the app's file list (CI run 36216364642).
set -euo pipefail
cd "$(dirname "$0")/.."
xcodegen dump --type json | python3 -c '
import json, sys
targets = json.load(sys.stdin)["targets"]
names = {}
for name, spec in targets.items():
    settings = (spec.get("settings") or {}).get("base") or {}
    module = settings.get("PRODUCT_MODULE_NAME") or settings.get("PRODUCT_NAME") or name
    for kind, value in (("target", name), ("module", module)):
        names.setdefault((kind, value.lower()), set()).add(value)
clashes = [(kind, sorted(found)) for (kind, _), found in names.items() if len(found) > 1]
for kind, found in clashes:
    print(f"{kind} names differ only by case: {found}")
if clashes:
    sys.exit(1)
print(f"target names: OK ({len(targets)} targets, no two differ only by case)")
'
