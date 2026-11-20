# Builds docs/decisions/pixelswitch-hand-checks-2.3-2026-11-20.html from the founder
# template (~/.claude/templates/founder-questions-template.html), changing only PAGE and
# <title>. Run it from anywhere:  python3 scripts/hand-checks/gen-hand-checks-2.3.py
# Then render both themes at 1920x1080 and look at them:
#   node scripts/hand-checks/render-hand-checks-2.3.cjs <out-dir>
# Portable like the 2.1 and 2.2 pairs: no path here is tied to one user's home folder.
import json, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = pathlib.Path.home() / ".claude/templates/founder-questions-template.html"
OUT = REPO / "docs/decisions/pixelswitch-hand-checks-2.3-2026-11-20.html"
ASKED = "2026-11-20T12:00:00-08:00"

SKIP = {"label": "C. Skipped for now", "note": "Fine. I will ask again later."}
NOTHING = "Counted as not checked yet. Anything wrong is fixed in the next version, which reaches you by itself."
NOTHING_S = "Counted as not checked yet; anything wrong is fixed in the next version."

def card(id, short, title, what, why, steps, recommend, options):
    return {"id": id, "asked": ASKED, "title": title, "short": short, "what": what, "why": why,
            "steps": steps, "nothing": NOTHING, "nothingShort": NOTHING_S,
            "recommend": recommend, "options": options}

cards = [
 card("version", "Version shows 2.3",
  "Does PixelSwitch say version 2.3 after it updates?",
  "2.3 installs itself through the updater (about every 6 hours, or when you click Install). Every other check on this page needs 2.3 to be the version running.",
  "Without this, the other checks would be looking at 2.2.",
  ["Click the PixelSwitch icon in the menu bar, open <b>Settings</b>, then the <b>About</b> tab.",
   "It should read <b>Version 2.3</b>. If it still says 2.2, click <b>Check for Updates</b> there, let it install and relaunch, and look again."],
  "Check the version first",
  [{"label": "A. Passed: it says Version 2.3", "note": "Pick this only if you saw it."},
   {"label": "B. It will not update, or says something else", "note": "Write what the About tab says in the box below."}, SKIP]),
 card("header", "Email shows once",
  "Does the header show ahmed@pixelventures.ai once, not twice?",
  "When an account's organization name is Anthropic's default (the account's email followed by 's Organization), 2.3 shows the email once in the popover header, the menu bar text and the widget. A real organization name (a Team) still shows above the email, and a label you chose still shows above the email.",
  "You asked for this on 2026-11-20: \"I don't want the email address to show 2 times\".",
  ["Open the popover while <b>ahmed@pixelventures.ai</b> is the active account and look at the header.",
   "The address should appear <b>once</b>, on one line. Look at the menu bar text too if it shows a name."],
  "Look at the header once",
  [{"label": "A. Passed: the address shows once", "note": "Pick this only if you saw it."},
   {"label": "B. It still shows twice, or wraps onto two lines", "note": "Say which account and where (header, menu bar or widget)."}, SKIP]),
 card("popover", "Usage and Costs tabs, bigger bottom icons",
  "Does the popover have only Usage and Costs, with bigger icons that explain themselves on hover?",
  "The popover's Accounts tab is gone. The footer now has five bigger icons: Add current account, Sign in a new account, Refresh, Settings and Quit. Resting the pointer on each should show its name.",
  "You asked for this on 2026-11-20: bigger, easier to see, with a hover label so people know what each one is for.",
  ["Open the popover. The tabs at the top should be <b>Usage</b> and <b>Costs</b> only.",
   "Look at the icons at the bottom: five of them, larger than in 2.2.",
   "Rest the pointer on each one for a second. A label should appear for all five."],
  "Look, then hover each icon",
  [{"label": "A. Passed: two tabs, five bigger icons, a label on each", "note": "Pick this only if all five labels appeared."},
   {"label": "B. Something is missing or hard to see", "note": "Say which icon had no label, or what looked wrong."}, SKIP]),
 card("add-sign-in", "Add current and Sign in a new account",
  "Do Add current account and Sign in a new account work from the bottom icons?",
  "These two icons do what the Accounts tab's buttons did. While a sign-in runs, the popover says <b>Signing in…</b> with a <b>Show sign-in window</b> button, and Add and Remove are off until it finishes.",
  "So you never need to open a tab to add or sign in an account.",
  ["<b>Add current account</b> saves the account Claude Code is signed in to right now. If it is already in your list, nothing new should appear. Click it and look at the account list in Settings → Accounts.",
   "<b>Sign in a new account:</b> click it and look for <b>Signing in…</b> and <b>Show sign-in window</b>. You can cancel the sign-in without finishing; nothing changes unless you complete it."],
  "Click each once; cancel the sign-in",
  [{"label": "A. Passed: both icons worked", "note": "Say in the box what each did."},
   {"label": "B. One of them did nothing or did the wrong thing", "note": "Say which one and what you saw."}, SKIP]),
 card("settings-accounts", "Settings → Accounts has the account actions",
  "Does Settings → Accounts have Switch, Re-sign and a \"...\" menu with Edit label and Remove, and do long addresses show in full?",
  "Everything the popover's Accounts tab did now lives in Settings → Accounts. Settings opens on the tab you used last, and the window is wider so a long address (for example beeco1@pixelventures.ai) is not cut off.",
  "You asked for this on 2026-11-20: move the accounts tab's functions into the Settings window.",
  ["Open <b>Settings</b> and go to <b>Accounts</b>.",
   "Each row should have <b>Switch</b> and <b>Re-sign</b> buttons and a <b>...</b> menu holding <b>Edit label</b> and <b>Remove</b>. Do not remove an account to test this.",
   "Check that the longest address shows in full, with no cut-off.",
   "Close Settings and open it again: it should reopen on Accounts."],
  "Open Settings and look at the rows",
  [{"label": "A. Passed: all buttons are there and every address shows in full", "note": "Pick this only if you saw all of it."},
   {"label": "B. Something is missing, cut off, or in the wrong place", "note": "Say what and on which row."}, SKIP]),
 card("reset-beeco1", "Limit resets as beeco1",
  "On beeco1@pixelventures.ai, does Limit resets open claude.ai's usage page in a Chrome profile?",
  ("The <b>Limit resets</b> button now opens that account's claude.ai usage page in Chrome, in the profile that belongs to the account (matched by the Google account in Chrome's profile list). If no profile matches, PixelSwitch creates one named <b>PixelSwitch beeco1@pixelventures.ai</b> the first time. "
   "<b>PixelSwitch only opens the page.</b> Whether to click claude.ai's own Reset button is your decision; neither PixelSwitch nor I ever do."),
  ("Anthropic offers resets only to claude.ai and Claude Code, so the page has to be claude.ai itself in a browser that already knows your logins. "
   "<b>Unproven until you try it:</b> whether each existing Chrome profile is signed in to the matching claude.ai account."),
  ["On the <b>beeco1@pixelventures.ai</b> card, click <b>Limit resets</b>.",
   "Chrome should open on claude.ai's usage page. Note <b>which Chrome profile</b> it is (the name at the top right of the Chrome window).",
   "If claude.ai asks you to sign in, sign in as beeco1@pixelventures.ai once in that profile, then click <b>Limit resets</b> again.",
   "Say in the box which profile opened and whether it was already signed in to beeco1. Do not click a reset just to test."],
  "Click it and note the profile",
  [{"label": "A. Passed: Chrome opened on the usage page, signed in as beeco1", "note": "Say which profile in the box."},
   {"label": "B. It opened, but I had to sign in to claude.ai once", "note": "Say which profile and that sign-in worked."},
   {"label": "C. It did not work", "note": "Say what happened (nothing opened, wrong profile, an error)."}]),
 card("reset-bws", "Limit resets as the Bespoke Woodcraft account",
  "On ahmed@bespokewoodcraftstudio.com, does Limit resets open your existing Chrome profile of that name?",
  "Same button, on the <b>ahmed@bespokewoodcraftstudio.com</b> card. You already have a Chrome profile called ahmed@bespokewoodcraftstudio.com, so PixelSwitch should open the usage page in that one and not create a new profile.",
  "This shows the profile matching works for a profile you made yourself.",
  ["On the <b>ahmed@bespokewoodcraftstudio.com</b> card, click <b>Limit resets</b>.",
   "Chrome should open claude.ai's usage page in the profile named <b>ahmed@bespokewoodcraftstudio.com</b>, with no new profile created.",
   "Say in the box which profile opened and whether it was signed in to claude.ai."],
  "Click it and note the profile",
  [{"label": "A. Passed: it opened in my existing profile", "note": "Say whether it was already signed in."},
   {"label": "B. It opened in a different or new profile", "note": "Say which one."},
   {"label": "C. It did not work", "note": "Say what happened."}]),
 card("expired", "Expired card says Click Re-sign to fix",
  "Does an expired card say \"Click Re-sign to fix.\", and does Re-sign work?",
  "The expired message used to end with (↻), which pointed at the Refresh icon. It now says <b>Click Re-sign to fix.</b> and the Re-sign button on the card opens the sign-in panel. A card that is expired also can no longer be switched to by double-clicking it.",
  "You pointed at the wrong icon on 2026-11-20; this is the fix.",
  ["Open the popover and look for a card that says its sign-in expired.",
   "<b>If one does:</b> read the message, click <b>Re-sign</b> on it and check the sign-in panel opens for that account. You can close it without signing in.",
   "<b>If none does today,</b> pick B below. It cannot be tested without an expired sign-in, and I do not want you to cause one."],
  "Test it only if a card is expired today",
  [{"label": "A. Passed: the message and Re-sign both worked", "note": "Say which account."},
   {"label": "B. Not testable today: no card is expired", "note": "Fine. It stays unproven until one is."},
   {"label": "C. It did not work", "note": "Say what the message said and what happened."}]),
]

page = {
 "key": "pixelswitch-hand-checks-2-3-2026-11-20",
 "heading": "PIXELSWITCH: HAND CHECKS FOR 2.3 (2026-11-20)",
 "eyebrow": "PixelSwitch · hand checks · version 2.3 · 2026-11-20",
 "title": "Hand checks for 2.3: one email, tidier popover, Limit resets in Chrome",
 "lede": [
  "<strong>Nothing on this page blocks the work.</strong> 2.3 is built, reviewed and released first and checked by you afterwards, as with every release since 1.2. "
  "Eight short checks: the version, the email shown once, the popover and its new icons, Settings → Accounts, Limit resets for two accounts, and the expired message.",
  "<b>2.3 replaces the 2.2 Limit resets window.</b> The reset checks on the 2.2 page (Limit resets as beeco1, still signed in, the other accounts, Google sign-in) no longer apply: that window is gone.",
  "<b>PixelSwitch never uses a reset.</b> Limit resets only opens claude.ai's usage page in Chrome; clicking a reset there is only ever your decision."
 ],
 "more": [
  "<b>What is in 2.3.</b> (1) Your email shows once when the organization name is Anthropic's default. (2) Limit resets opens the account's claude.ai usage page in the Chrome profile that belongs to it, and the 2.2 in-app window is removed. "
  "(3) The popover's Accounts tab is gone; Switch, Re-sign, Edit label and Remove are in Settings → Accounts. (4) Bigger bottom icons with hover labels, plus Add current account and Sign in a new account. (5) \"Click Re-sign to fix.\" and no double-click switching to an expired account.",
  "<b>Not in 2.3:</b> a one-click reset. Anthropic offers resets only to claude.ai and Claude Code, and PixelSwitch will not pretend to be either. Claude Code's own reset is switched off for the account that could be checked."
 ],
 "moreTitle": "What is in 2.3 (you do not need to read this)",
 "questions": cards,
}
t = TEMPLATE.read_text()
js = "var PAGE = " + json.dumps(page, ensure_ascii=False, indent=2) + ";\n\n"
start = t.index("var PAGE = {")
end = t.index("/* ================================== the page, rendered")
t = t[:start] + js + t[end:]
t = t.replace("<title>PROJECT: your decisions, round N</title>", "<title>PixelSwitch: hand checks for 2.3</title>")
OUT.write_text(t)
print("page written", len(cards), "cards ->", OUT)
