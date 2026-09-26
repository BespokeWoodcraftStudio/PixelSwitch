#!/bin/bash
# Remote-control integration check against the REAL running PixelSwitch.
#
# Safe on a Mac with real accounts. It NEVER switches, removes, relabels,
# reorders or re-thresholds an account. It only:
#   - reads status, accounts and usage;
#   - changes two harmless settings and puts each back (also on Ctrl-C);
#   - watches events while asking for one refresh;
#   - starts a sign-in and cancels it (starting and cancelling
#     `claude auth login` changes no login; the sign-in window appears on the
#     Mac for a moment). Skip that part with SKIP_SIGN_IN=1;
#   - talks to `pixelswitch mcp` over stdio.
#
# Needs PixelSwitch 1.2 or later running, and the command-line tool installed
# (Settings → Claude CLI → Install), or PIXELSWITCH_CLI pointing at it.
set -uo pipefail

CLI="${PIXELSWITCH_CLI:-$HOME/.local/bin/pixelswitch}"
WORK=$(mktemp -d)
pass=0
fail=0
ok()  { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1${2:+: $2}"; fail=$((fail + 1)); }
# field KEY [KEY...]: prints the JSON value at that path ("" for null); "#" prints a list's length.
field() {
    python3 -c '
import json, sys
d = json.load(sys.stdin)
for key in sys.argv[1:]:
    d = len(d) if key == "#" else (d[int(key)] if isinstance(d, list) else d.get(key))
print("" if d is None else d)' "$@"
}

[ -x "$CLI" ] || { echo "No pixelswitch at $CLI. Install it from Settings → Claude CLI, or set PIXELSWITCH_CLI."; exit 2; }

ORIGINAL_DRAIN=""
ORIGINAL_LOW=""
restore() {
    [ -n "$ORIGINAL_DRAIN" ] && "$CLI" settings set autoSwitch.drainWithinHours "$ORIGINAL_DRAIN" >/dev/null 2>&1
    [ -n "$ORIGINAL_LOW" ] && "$CLI" settings set menuBar.lowRemainingWarningThreshold "$ORIGINAL_LOW" >/dev/null 2>&1
    rm -rf "$WORK"
}
trap restore EXIT

# 1. Status and protocol
if status=$("$CLI" --json status); then
    [ "$(echo "$status" | field protocolVersion)" = "1" ] && ok "status answers, protocol 1" || bad "status protocol" "$status"
else
    bad "status" "exit $?"
fi

# 2. Accounts and usage (read only)
accounts=$("$CLI" --json accounts) && count=$(echo "$accounts" | field accounts '#') \
    && ok "accounts lists $count account(s)" || bad "accounts"
usage=$("$CLI" --json usage) && echo "$usage" | field machine todayCost >/dev/null \
    && ok "usage reports every account and this Mac's cost" || bad "usage"
"$CLI" usage nobody-at-all-xyz >/dev/null 2>&1; code=$?
[ "$code" = "4" ] && ok "an unknown account exits 4" || bad "unknown account exit code" "$code"

# 3. Two harmless settings, each changed and put back
ORIGINAL_DRAIN=$("$CLI" --json settings autoSwitch.drainWithinHours | field settings autoSwitch.drainWithinHours)
target=13; [ "${ORIGINAL_DRAIN%.*}" = "13" ] && target=14
now=$("$CLI" --json settings set autoSwitch.drainWithinHours "$target" | field settings autoSwitch.drainWithinHours)
[ "${now%.*}" = "$target" ] && ok "a setting changes (autoSwitch.drainWithinHours → $target)" || bad "setting change" "$now"
"$CLI" settings set autoSwitch.drainWithinHours "$ORIGINAL_DRAIN" >/dev/null
back=$("$CLI" --json settings autoSwitch.drainWithinHours | field settings autoSwitch.drainWithinHours)
[ "$back" = "$ORIGINAL_DRAIN" ] && ok "and is put back ($ORIGINAL_DRAIN)" || bad "setting restore" "$back"

ORIGINAL_LOW=$("$CLI" --json settings menuBar.lowRemainingWarningThreshold | field settings menuBar.lowRemainingWarningThreshold)
"$CLI" settings set menuBar.lowRemainingWarningThreshold 35 >/dev/null && \
"$CLI" settings set menuBar.lowRemainingWarningThreshold "$ORIGINAL_LOW" >/dev/null && \
[ "$("$CLI" --json settings menuBar.lowRemainingWarningThreshold | field settings menuBar.lowRemainingWarningThreshold)" = "$ORIGINAL_LOW" ] \
    && ok "a menu bar setting round-trips" || bad "menu bar setting round trip"

"$CLI" settings set refreshInterval 7 >/dev/null 2>&1; code=$?
[ "$code" = "2" ] && ok "an invalid value is refused (exit 2)" || bad "invalid value exit code" "$code"

# 4. Events while one refresh runs
"$CLI" watch > "$WORK/events" 2>/dev/null &
watcher=$!
sleep 1
"$CLI" refresh >/dev/null 2>&1
sleep 3
kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
grep -q '"usageUpdated"' "$WORK/events" && ok "watch reports usageUpdated after a refresh" || bad "watch" "$(head -c 300 "$WORK/events")"

# 5. Sign-in: start, then cancel
if [ "${SKIP_SIGN_IN:-0}" != "1" ]; then
    started=$("$CLI" --json accounts sign-in)
    state=$(echo "$started" | field state)
    link=$(echo "$started" | field automaticLink)
    case "$state" in
        waitingForUser|starting) ok "a sign-in starts ($state)";;
        *) bad "sign-in start" "$state";;
    esac
    [[ "$link" == https://*localhost* || "$link" == https://*redirect_uri=http%3A%2F%2Flocalhost* ]] \
        && ok "the automatic link finishes on this Mac (localhost redirect)" || bad "automatic link" "${link:0:80}"
    "$CLI" accounts sign-in cancel >/dev/null
    for _ in 1 2 3 4 5 6 7 8; do
        state=$("$CLI" --json accounts sign-in status | field signIn state)
        [ "$state" = "cancelled" ] && break
        sleep 1
    done
    [ "$state" = "cancelled" ] && ok "the sign-in is cancelled and nothing changed" || bad "sign-in cancel" "$state"
fi

# 6. MCP over stdio
printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"it","version":"1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status"}}' \
    | "$CLI" mcp > "$WORK/mcp"
python3 - "$WORK/mcp" <<'PY' && ok "MCP: handshake, 18 tools, and a working tool call" || bad "MCP" "$(head -c 300 "$WORK/mcp")"
import json, sys
replies = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
assert len(replies) == 3
assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
assert len(replies[1]["result"]["tools"]) == 18
assert replies[2]["result"]["isError"] is False
PY

echo
echo "$pass passed, $fail failed"
[ "$fail" = "0" ]
