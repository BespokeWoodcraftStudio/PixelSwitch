# Builds docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html from the founder
# template (~/.claude/templates/founder-questions-template.html), changing only PAGE and
# <title>. Run it from anywhere:  python3 scripts/hand-checks/gen-hand-checks-2.1.py
# Then render both themes at 1920x1080 and look at them:
#   node scripts/hand-checks/render-hand-checks-2.1.cjs <out-dir>
# Unlike gen-hand-checks.py (the 2026-09-25 page), no path here is tied to one user's home folder.
import json, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = pathlib.Path.home() / ".claude/templates/founder-questions-template.html"
OUT = REPO / "docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html"
IMG = "../investigations/2026-11-10-readability-ultra-auth/evidence/"
ASKED = "2026-11-10T07:52:00-08:00"

GREEN = "#2ea043"   # the new
RED = "#e5484d"     # the old

def box(left, top, width, height, color, dashed=False):
    return ('<span style="position:absolute;left:%s%%;top:%s%%;width:%s%%;height:%s%%;'
            'border:3px %s %s;border-radius:8px;box-sizing:border-box;pointer-events:none"></span>'
            % (left, top, width, height, "dashed" if dashed else "solid", color))

def pic(name, alt, caption, boxes="", outline=True):
    """One render, clickable to its full size. boxes are the red/green marks laid over it."""
    frame = "outline:3px solid %s;outline-offset:2px;" % GREEN if outline else ""
    return ('<span style="display:block;flex:1 1 220px;min-width:0">'
            '<a href="%s%s.png" target="_blank" rel="noopener" style="display:block;position:relative;line-height:0;%s">'
            '<img src="%s%s.png" alt="%s" style="width:100%%;height:auto;display:block;border-radius:6px">%s</a>'
            '<span style="display:block;font-size:14px;margin-top:6px">%s</span></span>'
            % (IMG, name, frame, IMG, name, alt, boxes, caption))

def pair(a, b):
    return '<span style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 6px">%s%s</span>' % (a, b)

chip_green = '<span style="border:2px solid %s;border-radius:4px;padding:0 6px">green</span>' % GREEN
chip_red = '<span style="border:2px dashed %s;border-radius:4px;padding:0 6px">red dashed</span>' % RED

# Card 1: the new look of the Usage card, light and dark. There is no render of the old look.
readable_pics = pair(
    pic("limits-light", "The Usage card in light mode, example account", "Light mode (new). Click to see it full size."),
    pic("limits-dark", "The Usage card in dark mode, example account", "Dark mode (new). Click to see it full size."))

# Card 2: an Ultra card above a Max card. Green marks the new, red dashed the row an Ultra account no longer has.
def ultra_marks():
    return (box(83.4, 10.4, 9.7, 3.3, GREEN)          # the Ultra badge
            + box(10.5, 15.9, 82.6, 20.1, GREEN)       # Weekly and Fable only
            + box(10.5, 58.6, 82.6, 9.1, RED, True))   # the Session row an Ultra card does not have
ultra_pics = pair(
    pic("ultra-light", "An Ultra card above a Max card, light mode, example accounts",
        "Light mode. Click to see it full size.", ultra_marks(), outline=False),
    pic("ultra-dark", "An Ultra card above a Max card, dark mode, example accounts",
        "Dark mode. Click to see it full size.", ultra_marks(), outline=False))

PASS_NOTE = "Pick this only if every step matched."
cards = [
 {
  "id": "readable", "asked": ASKED,
  "title": "Is the popover readable now, in light and in dark?",
  "short": "Popover readable, light and dark",
  "what": ("You said the menu bar window was next to impossible to read because of its background. 2.1 makes the window, its cards, "
           "usage bars and badges solid, so nothing from your desktop shows through, and it makes the text stronger, in both light and dark. "
           "The pictures are renders of the new Usage card made by the app's own views, with example accounts (not yours)."
           + readable_pics +
           "<b>About the marks:</b> there is no picture of the old look, so I cannot mark the old in red. Each picture has a "
           + chip_green + " outline because the whole card is new. The real window's frame (the arrow and rounded corners) cannot be drawn offscreen, "
           "so the pictures do not show it."),
  "why": ("Only your eyes on your own screen and wallpaper can say whether it is comfortable. By the numbers, measured on the rendered pixels: "
          "every word is at least 4.5 to 1 against its background and every bar and outline at least 3 to 1, in both light and dark. "
          "Two things were left as they were: the white labels on the orange buttons (3.1 to 1), and any bar colors you chose yourself."),
  "steps": ["Settings → About reads <b>Version 2.1</b>. (2.1 installs itself after it is released; check it before the steps below.)",
            "System Settings → Appearance → <b>Light</b>. Click the PixelSwitch icon in the menu bar, then look at the <b>Usage</b>, <b>Costs</b> and <b>Accounts</b> tabs.",
            "Look for: the window is solid (put a busy picture or window behind it, and nothing shows through); you can read account names, "
            "<b>Resets in …</b>, the percentages, and the <b>Max</b>, <b>Ultra</b> and <b>Active</b> badges without effort; each bar stands out from its track; "
            "the orange outline around the account you are using is clear.",
            "Switch to <b>Dark</b> and look at the same three tabs for the same things.",
            "Optional: open Settings and see whether the grey help text under each control is easier to read than before."],
  "nothing": "Counted as not checked yet. If something is still hard to read, it is fixed in the next version, which reaches you by itself.",
  "nothingShort": "Counted as not checked yet; anything hard to read is fixed in the next version.",
  "recommend": "Look in light, then dark, and tell me what you saw",
  "options": [
    {"label": "A. Passed: easy to read in both light and dark", "note": "Pick this only if you looked at both."},
    {"label": "B. Something is still hard to read", "note": "Say which mode (light or dark), which tab and which words in the box below. A screenshot path helps."},
    {"label": "C. Skipped for now", "note": "Fine. I will ask again later."},
  ],
 },
 {
  "id": "ultra", "asked": ASKED,
  "title": "Does an Ultra account show Ultra and no 5-hour row?",
  "short": "Ultra badge, no 5-hour row",
  "what": ("Anthropic removed the 5-hour limit from the Max 20x plan, now called Ultra. 2.1 calls such an account <b>Ultra</b> and stops showing, "
           "tracking or acting on a 5-hour limit for it: no Session row on its card, no Session item in the menu bar while it is the account you are using, "
           "no Session views in the widget, and auto-switch follows its weekly limit alone. If PixelSwitch cannot tell an account's plan, that account keeps the 5-hour limit. "
           "If Anthropic ever reports a real 5-hour reading for an Ultra account, PixelSwitch shows it again."
           + ultra_pics +
           "<b>About the marks:</b> the " + chip_green + " boxes are what is new (the Ultra badge, and a card with only Weekly and Fable). "
           "The " + chip_red + " box is the row an Ultra card used to have and no longer does; it is drawn on the Max card below only because "
           "that is where the row still exists. These are renders with example accounts."),
  "why": ("Before 2.1 an Ultra account still showed a 5-hour row that had no number to show, and the plan badge said Max. "
          "Anthropic has sent no 5-hour reading for any of your accounts in the logs on your Mac, which start on 2026-11-03. "
          "A non-active account is recognized from its saved login the first time PixelSwitch reads it after it starts, so if one still says Max, wait one refresh (about 5 minutes)."),
  "steps": ["Open the popover and look at the account you are using: the plan badge reads <b>Ultra</b> and the card shows <b>Weekly</b> and <b>Fable</b> only, with no <b>Session</b> row.",
            "Click the <b>Accounts</b> tab: each Ultra account's badge reads Ultra.",
            "Settings → Accounts: an Ultra account's row says <b>No 5-hour limit</b> where the Session menu used to be.",
            "In Terminal: <code>/Applications/PixelSwitch.app/Contents/Helpers/pixelswitch accounts</code>. The Session column reads <code>n/a</code> for an Ultra account, and Switches at shows one number (the weekly one) instead of two.",
            "If you have an account that is not Ultra (Max 5x or Pro), it keeps its Session row and its Max or Pro badge."],
  "nothing": "Counted as not checked yet. Anything that is wrong is fixed in the next version, which reaches you by itself.",
  "nothingShort": "Counted as not checked yet; anything wrong is fixed in the next version.",
  "recommend": "Look at one Ultra account, the Accounts tab and the CLI",
  "options": [
    {"label": "A. Passed: Ultra badge and no 5-hour row everywhere I looked", "note": PASS_NOTE},
    {"label": "B. Something is wrong", "note": "Say which account, where you saw a 5-hour row or a Max badge, and what the account is (Max 20x or something else)."},
    {"label": "C. Skipped for now", "note": "Fine. I will ask again later."},
  ],
 },
 {
  "id": "signin-expiry", "asked": ASKED,
  "title": "Sign-in expiry: what 2.1 now does (nothing to do by hand)",
  "short": "Sign-in expiry (information)",
  "what": ("You asked that PixelSwitch never switch onto an account that needs signing in again, and that it keeps watching the account you are using "
           "and moves you if its sign-in dies. 2.1 does both. <b>There is nothing for you to click:</b> the only way to test the second part by hand would be to sign Claude Code out on purpose, which I do not want you to do."
           "<ul style='margin:8px 0 0;padding-left:20px'>"
           "<li><b>Before every switch,</b> PixelSwitch renews the saved sign-in of the account it is about to switch to. If Anthropic refuses it, that account is marked as needing a sign-in and the next account is tried. If Anthropic cannot be reached, the switch goes ahead as before.</li>"
           "<li><b>Every 30 seconds</b> it looks at the sign-in Claude Code is using. It counts as dead only when Claude Code has signed that account out (the login is blank) <i>and</i> <code>claude auth status</code> agrees, on two looks at least 15 seconds apart. "
           "An expired token on its own, no internet, a busy server or a slow answer never counts.</li>"
           "<li><b>With auto-switch on,</b> a dead sign-in moves you to an account that works within about a minute, even if the account you are on is set to Manual only (you cannot work on a signed-out account). "
           "<b>With auto-switch off,</b> nothing moves: the account is marked and the log says so once.</li>"
           "</ul>"),
  "why": ("Your Claude sign-in on one account (ahamade@gmail.com) died at 2026-11-10T10:40Z. The 2.0 app never noticed it and stayed on that account until you switched by hand at 2026-11-10T13:39Z, about three hours later. "
          "That account is the one you switched back to, and its saved copy probably holds the sign-in Anthropic had just refused. "
          "If that happens again with 2.1, it leaves that account within about a minute and the account shows <b>Session expired. Re-authenticate (↻) to fix.</b> "
          "To fix such an account, click the refresh button (↻) on its row in the Accounts tab, or run <code>pixelswitch accounts reauth &lt;account&gt;</code>."
          "<br><br><b>What you would see in <code>~/Library/Logs/PixelSwitch-app.log</code></b> if it ever happens: lines starting <code>[authWatch]</code> "
          "(first <i>looks signed out … confirming on the next look</i>, then <i>is signed out … marking it as needing sign-in</i>, then <i>Auto-switch will move you off …</i>), "
          "then <code>[autoSwitch] Rule needsSignIn</code> and <code>Switching to</code>. "
          "It cannot be seen by looking: the watch only speaks when a sign-in dies."),
  "nothing": "Nothing happens. The watch runs by itself and there is nothing for you to do; the card counts as read.",
  "nothingShort": "Nothing happens; the watch runs by itself and the card counts as read.",
  "recommend": "Read it; nothing to do by hand",
  "options": [
    {"label": "A. Understood, nothing for me to do", "note": "The watch runs by itself."},
    {"label": "B. I want it to behave differently", "note": "For example, not leaving an account set to Manual only. Say how in the box below; behavior changes ship in the next version."},
  ],
 },
]

page = {
 "key": "pixelswitch-hand-checks-2-1-2026-11-10",
 "heading": "PIXELSWITCH: HAND CHECKS FOR 2.1 (2026-11-10)",
 "eyebrow": "PixelSwitch · hand checks · version 2.1 · 2026-11-10",
 "title": "Hand checks for 2.1: readable colors, Ultra accounts, sign-in expiry",
 "lede": [
  "<strong>Nothing on this page blocks the work.</strong> 2.1 is built and merged, with every automatic check and a real build passing. It is released first and checked by you afterwards, as with every release since 1.2. "
  "Cards 1 and 2 are things only your eyes can judge. Card 3 is information and needs nothing from you.",
  "To check, 2.1 has to be the version running: Settings → About reads <b>Version 2.1</b>. It installs itself through the updater after it is released.",
  "<b>About the pictures:</b> they are renders made by the app's own views with example accounts, not your data and not the real window frame. "
  "There is no picture of the old look, so the old cannot be marked in red there. Where a change can be drawn (card 2), the new is outlined in green and the old in red dashes."
 ],
 "more": [
  "<b>What is in 2.1.</b> (1) The menu bar window, its cards, bars and badges are solid and easier to read, in light and dark. (2) Ultra (Max 20x) accounts show Ultra and have no 5-hour limit anywhere. "
  "(3) PixelSwitch renews a target's saved sign-in before every switch, skips an account that Anthropic refuses, and watches the live sign-in every 30 seconds. "
  "(4) A small change to how a renewed sign-in is saved (it is saved before anything else happens, and a slow renewal gives up after 15 seconds); you do not need to check this.",
  "<b>Not in 2.1:</b> the Linux version is unchanged and does not have these three changes yet."
 ],
 "moreTitle": "What is in 2.1 (you do not need to read this)",
 "questions": cards,
}
t = TEMPLATE.read_text()
js = "var PAGE = " + json.dumps(page, ensure_ascii=False, indent=2) + ";\n\n"
start = t.index("var PAGE = {")
end = t.index("/* ================================== the page, rendered")
t = t[:start] + js + t[end:]
t = t.replace("<title>PROJECT: your decisions, round N</title>", "<title>PixelSwitch: hand checks for 2.1</title>")
OUT.write_text(t)
print("page written", len(cards), "cards ->", OUT)
