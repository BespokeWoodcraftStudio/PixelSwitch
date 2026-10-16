#!/bin/bash
# Type-check app sources with every file using SwiftUI property-wrapper macros last.
set -uo pipefail
cd "$1"
SDK=$(xcrun --show-sdk-path)
LATE=$(grep -lE '@State\b|@Binding\b|@Environment\b|@FocusState\b|@AppStorage\b|@StateObject\b|@ObservedObject\b|@EnvironmentObject\b|#Preview' $(find PixelSwitch Shared -name '*.swift') | sort)
FILES=()
while IFS= read -r f; do echo "$LATE" | grep -qx "$f" || FILES+=("$f"); done < <(find PixelSwitch Shared -name '*.swift' | sort)
echo "early files: ${#FILES[@]}  late files: $(echo "$LATE" | wc -l)"
while IFS= read -r f; do FILES+=("$f"); done <<< "$LATE"
swiftc -typecheck -swift-version 6 -target arm64-apple-macos14.0 -sdk "$SDK" -F "$HOME/Library/Caches/pixelswitch-dev/sparkle-2.9.0" "${FILES[@]}" 2>&1 | grep -E '^[^ ].*error:' | sort -u
