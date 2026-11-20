# Builds docs/decisions/pixelswitch-hand-checks-2.4-2026-11-20.html from the founder
# template (~/.claude/templates/founder-questions-template.html), changing only PAGE and
# <title>. Run it from anywhere:  python3 scripts/hand-checks/gen-hand-checks-2.4.py
# Then render both themes at 1920x1080 and look at them:
#   node scripts/hand-checks/render-hand-checks-2.4.cjs <out-dir>
# Portable like the 2.1 and 2.2 pairs: no path here is tied to one user's home folder.
import json, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = pathlib.Path.home() / ".claude/templates/founder-questions-template.html"
OUT = REPO / "docs/decisions/pixelswitch-hand-checks-2.4-2026-11-20.html"
ASKED = "2026-11-20T10:45:00-08:00"

SKIP = {"label": "C. Skipped for now", "note": "Fine. I will ask again later."}
NOTHING = "Counted as not checked yet. Anything wrong is fixed in the next version, which reaches you by itself."
NOTHING_S = "Counted as not checked yet; anything wrong is fixed in the next version."

def card(id, short, title, what, why, steps, recommend, options):
    return {"id": id, "asked": ASKED, "title": title, "short": short, "what": what, "why": why,
            "steps": steps, "nothing": NOTHING, "nothingShort": NOTHING_S,
            "recommend": recommend, "options": options}

cards = [
 card("version", "Version shows 2.4",
  "Does PixelSwitch say version 2.4?",
  "2.4 installs itself through the updater (about every 6 hours, or when you click Install). If 2.3 is stuck and never shows in your menu bar, install 2.4 by hand from the release page instead.",
  "Every other check on this page needs 2.4 to be the version running.",
  ["Click the PixelSwitch icon in the menu bar, open <b>Settings</b>, then the <b>About</b> tab.",
   "It should read <b>Version 2.4</b>. If it still says 2.3, click <b>Check for Updates</b>, or download 2.4 from the release page and drag it into Applications."],
  "Check the version first",
  [{"label": "A. Passed: it says Version 2.4", "note": "Pick this only if you saw it."},
   {"label": "B. It will not update, or says something else", "note": "Write what the About tab says in the box below."}, SKIP]),
 card("menubar", "Shows in the menu bar and stays",
  "Does PixelSwitch show in the menu bar, and is it still there after you quit and open it again?",
  "2.3 crashed at launch on any Mac that had opened the 2.2 window, so the icon never appeared. 2.4 no longer touches that data at launch. It was launched with the broken 2.3 state planted and stayed up.",
  "This is the crash you reported this morning. I want you to see it fixed on your own Mac.",
  ["Look for the PixelSwitch icon or text in the menu bar.",
   "Quit PixelSwitch from the popover's Quit icon, then open it again from Applications.",
   "The icon should come back, and stay."],
  "Quit it once and reopen it",
  [{"label": "A. Passed: it shows, and it came back after a restart", "note": "Pick this only if you did the restart."},
   {"label": "B. It is missing or disappeared", "note": "Say when it disappeared."}, SKIP]),
 card("icons", "Five icons on each Settings account row",
  "In Settings → Accounts, does each row have five icons that work with one click each?",
  "You asked why the actions were behind three dots. 2.4 shows plain icons instead: Switch, Re-sign, Edit label, the Chrome profile globe, and Remove.",
  "You asked for this on 2026-11-20 (check 5's comment).",
  ["Open <b>Settings</b>, then <b>Accounts</b>.",
   "Each row should show <b>five icons</b>: Switch, Re-sign, Edit label, a globe, Remove.",
   "Click Edit label on one row to see it works (you can cancel). Do not click Remove just to test."],
  "Look at the rows and click Edit label",
  [{"label": "A. Passed: five icons on each row, one click each", "note": "Pick this only if you saw all five."},
   {"label": "B. An icon is missing or does nothing", "note": "Say which icon on which row."}, SKIP]),
 card("hover", "Hover labels appear every time",
  "Does hovering any footer icon or Settings icon show its label every time?",
  "You said the labels \"work sometimes\". The system tooltip is unreliable in a menu bar popover, so 2.4 draws its own label on hover for the footer icons and for the icons in Settings.",
  "A label that sometimes shows is worse than none: you cannot tell what an icon does.",
  ["Open the popover and rest the pointer on each footer icon, five times each, including right after you open the popover.",
   "Do the same for the icons on a row in Settings → Accounts.",
   "A label should show every time."],
  "Hover each icon, five times",
  [{"label": "A. Passed: a label showed every time", "note": "Pick this only if you tried several times."},
   {"label": "B. Some labels did not show", "note": "Say which icon and when (for example right after opening the popover)."}, SKIP]),
 card("reset-bws", "Limit resets as the Bespoke Woodcraft account",
  "On ahmed@bespokewoodcraftstudio.com, does Limit resets open claude.ai's usage page in your profile of that name?",
  "I pointed this account at your existing Chrome profile <b>ahmed@bespokewoodcraftstudio.com</b> (the one Chrome calls Profile 2). Chrome does not let PixelSwitch read its profile list, so the choice is stored in PixelSwitch.",
  "Check 7 failed in 2.3 because PixelSwitch could not match the profile.",
  ["On the <b>ahmed@bespokewoodcraftstudio.com</b> card, click <b>Limit resets</b>.",
   "Chrome should open claude.ai's usage page in the profile named <b>ahmed@bespokewoodcraftstudio.com</b>.",
   "Do not click a reset just to test. Every reset is your own click."],
  "Click it and note the profile",
  [{"label": "A. Passed: it opened in my ahmed@bespokewoodcraftstudio.com profile", "note": "Say whether it was already signed in."},
   {"label": "B. It opened in a different or new profile", "note": "Say which one."},
   {"label": "C. It did not work", "note": "Say what happened."}]),
 card("reset-px", "Limit resets as ahmed@pixelventures.ai",
  "On ahmed@pixelventures.ai, does Limit resets open claude.ai's usage page in your profile of that name?",
  "Same button on the <b>ahmed@pixelventures.ai</b> card. It is pointed at your existing Chrome profile <b>ahmed@pixelventures.ai</b> (Profile 1), not the empty \"PixelSwitch ahmed@pixelventures.ai\" profile that 2.3 made by mistake.",
  "In 2.3 this opened an empty profile on the login page.",
  ["On the <b>ahmed@pixelventures.ai</b> card, click <b>Limit resets</b>.",
   "Chrome should open the usage page in the profile named <b>ahmed@pixelventures.ai</b>.",
   "You can delete the empty \"PixelSwitch ahmed@pixelventures.ai\" Chrome profile: nothing uses it now."],
  "Click it and note the profile",
  [{"label": "A. Passed: it opened in my ahmed@pixelventures.ai profile", "note": "Say whether it was already signed in."},
   {"label": "B. It opened in a different or new profile", "note": "Say which one."},
   {"label": "C. It did not work", "note": "Say what happened."}]),
 card("reset-beeco1", "Limit resets as beeco1",
  "On beeco1@pixelventures.ai, does Limit resets open its own PixelSwitch Chrome profile, and is the second click signed in?",
  "beeco1 has no Chrome profile of its own, so PixelSwitch opens a separate one (Chrome shows it as \"Person 2\") where you sign in to claude.ai once. 2.4 never falls back to some other account's profile.",
  "In 2.3 this took you to the claude login page in a new window, which is expected the first time only.",
  ["On the <b>beeco1@pixelventures.ai</b> card, click <b>Limit resets</b>. A \"Person 2\" Chrome window opens.",
   "Sign in to claude.ai as beeco1@pixelventures.ai there, once.",
   "Click <b>Limit resets</b> again: it should land on the usage page, already signed in."],
  "Click it, sign in once, click again",
  [{"label": "A. Passed: the second click landed on the usage page signed in", "note": "Say which profile opened."},
   {"label": "B. It opened the wrong profile, or asked me to sign in again", "note": "Say what you saw."},
   {"label": "C. It did not work", "note": "Say what happened."}]),
 card("globe", "The globe menu shows the choice for each account",
  "Does the globe icon on each Settings row show the Chrome profile choice for that account?",
  "The globe on each account row lets you pick which Chrome profile Limit resets uses: a profile it knows, Automatic, or a PixelSwitch profile. It shows a tick on the current choice.",
  "If a match is ever wrong, you fix it here in two clicks without anyone's help.",
  ["In <b>Settings → Accounts</b>, click the globe on the ahmed@bespokewoodcraftstudio.com row.",
   "A menu shows the profiles with a tick on the one in use. Close it without changing anything.",
   "Look at the globe on one other row."],
  "Open the globe on two rows",
  [{"label": "A. Passed: the menu shows a choice with a tick on each row", "note": "Say what the tick was on."},
   {"label": "B. The menu is empty, wrong or confusing", "note": "Say what it showed."}, SKIP]),
]

page = {
 "key": "pixelswitch-hand-checks-2-4-2026-11-20",
 "heading": "PIXELSWITCH: HAND CHECKS FOR 2.4 (2026-11-20)",
 "eyebrow": "PixelSwitch · hand checks · version 2.4 · 2026-11-20",
 "title": "Hand checks for 2.4: the crash is fixed, Limit resets in the right Chrome profile",
 "lede": [
  "<strong>Nothing on this page blocks the work.</strong> 2.4 is built, reviewed, launch-tested and released first and checked by you afterwards. "
  "Eight short checks: the version, the menu bar icon, the five Settings icons, hover labels, Limit resets for three accounts, and the globe menu.",
  "<b>If 2.3 never appears in your menu bar,</b> download and install 2.4 from the release page by hand: 2.3 can crash before it can update itself.",
  "<b>PixelSwitch never uses a reset.</b> Limit resets only opens claude.ai's usage page in Chrome. Every reset is your own click, and I never press one."
 ],
 "more": [
  "<b>What is in 2.4.</b> (1) The launch crash is fixed: the clean-up of the 2.2 window's data no longer uses WebKit at all, and it was run with the broken state planted. "
  "(2) Limit resets uses a Chrome profile choice stored per account (macOS stops PixelSwitch reading Chrome's profile list, and Chrome silently uses the last-used profile for an email it cannot match, so 2.4 never sends an email alone). "
  "(3) Settings account rows have five plain icons instead of the three-dot menu. (4) The hover labels are drawn by PixelSwitch, so they show every time.",
  "<b>Not in 2.4:</b> a one-click reset. Anthropic offers resets only to claude.ai and Claude Code, and PixelSwitch will not pretend to be either."
 ],
 "moreTitle": "What is in 2.4 (you do not need to read this)",
 "questions": cards,
}
t = TEMPLATE.read_text()
js = "var PAGE = " + json.dumps(page, ensure_ascii=False, indent=2) + ";\n\n"
start = t.index("var PAGE = {")
end = t.index("/* ================================== the page, rendered")
t = t[:start] + js + t[end:]
t = t.replace("<title>PROJECT: your decisions, round N</title>", "<title>PixelSwitch: hand checks for 2.4</title>")
OUT.write_text(t)
print("page written", len(cards), "cards ->", OUT)
