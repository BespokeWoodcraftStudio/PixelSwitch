# Builds docs/decisions/pixelswitch-hand-checks-2.2-2026-11-19.html from the founder
# template (~/.claude/templates/founder-questions-template.html), changing only PAGE and
# <title>. Run it from anywhere:  python3 scripts/hand-checks/gen-hand-checks-2.2.py
# Then render both themes at 1920x1080 and look at them:
#   node scripts/hand-checks/render-hand-checks-2.2.cjs <out-dir>
# Portable like the 2.1 pair: no path here is tied to one user's home folder.
import json, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = pathlib.Path.home() / ".claude/templates/founder-questions-template.html"
OUT = REPO / "docs/decisions/pixelswitch-hand-checks-2.2-2026-11-19.html"
IMG = "../evidence/2026-11-19-claude-web/"
ASKED = "2026-11-19T22:30:00-08:00"
GREEN = "#2ea043"

def pic(name, alt, caption):
    return ('<span style="display:block;max-width:520px;margin:10px 0 6px">'
            '<a href="%s%s.png" target="_blank" rel="noopener" style="display:block;line-height:0;outline:3px solid %s;outline-offset:2px">'
            '<img src="%s%s.png" alt="%s" style="width:100%%;height:auto;display:block;border-radius:6px"></a>'
            '<span style="display:block;font-size:14px;margin-top:6px">%s</span></span>'
            % (IMG, name, GREEN, IMG, name, alt, caption))

signin_pic = pic("signin-page-in-window", "The Limit resets window on claude.ai's sign-in, with an example account",
                 "What the window should look like when you are signed out (example account, not yours; the whole window is new, so it is outlined in green). Click to see it full size.")

SKIP = {"label": "C. Skipped for now", "note": "Fine. I will ask again later."}
NOTHING = "Counted as not checked yet. Anything wrong is fixed in the next version, which reaches you by itself."
NOTHING_S = "Counted as not checked yet; anything wrong is fixed in the next version."

cards = [
 {
  "id": "version", "asked": ASKED,
  "title": "Does PixelSwitch say version 2.2 after it updates?",
  "short": "Version shows 2.2",
  "what": "2.2 installs itself through the updater (about every 6 hours, or when you click Install). Every other check on this page needs 2.2 to be the version running.",
  "why": "Without this, the other checks would be looking at 2.1.",
  "steps": ["Click the PixelSwitch icon in the menu bar, open <b>Settings</b>, then the <b>About</b> tab.",
            "It should read <b>Version 2.2</b>. If it still says 2.1, click <b>Check for Updates</b> there, let it install and relaunch, and look again."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Check the version first",
  "options": [{"label": "A. Passed: it says Version 2.2", "note": "Pick this only if you saw it."},
              {"label": "B. It will not update, or says something else", "note": "Write what the About tab says in the box below."}, SKIP],
 },
 {
  "id": "resign", "asked": ASKED,
  "title": "Does Re-sign open the sign-in panel on a card whose sign-in expired?",
  "short": "Re-sign button on an expired card",
  "what": ("2.2 puts a <b>Re-sign</b> button on a main-window card that says the account's sign-in expired. It does the same thing as the refresh button (↻) in the Accounts tab. "
           "It does <b>not</b> appear on an account with no subscription (that card shows a grey <b>No plan</b> chip, see the chips check)."),
  "why": "Before 2.2 you had to go to the Accounts tab to re-sign an expired account. This puts the fix on the card that shows the problem.",
  "steps": ["Open the popover and look at each card for the words <b>Session expired</b>.",
            "<b>If a card says it,</b> click <b>Re-sign</b> on that card. The sign-in panel for that account should open (the same one the ↻ button in the Accounts tab opens). You can close the panel without signing in; nothing changes unless you finish a sign-in.",
            "<b>If no card says it today,</b> pick <b>B</b> below. It cannot be tested without an expired sign-in, and I do not want you to cause one on purpose."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Test it only if a card is expired today",
  "options": [{"label": "A. Passed: Re-sign opened the sign-in panel", "note": "Say which account in the box below."},
              {"label": "B. Not testable today: no card says the sign-in expired", "note": "Fine. It stays unproven until one does."},
              {"label": "C. It did not work", "note": "Say what happened (nothing opened, wrong account, an error) in the box below."}],
 },
 {
  "id": "chips", "asked": ASKED,
  "title": "Are the plan chips the right colors?",
  "short": "Plan chip colors",
  "what": ("Each plan chip has its own color now: Ultra orange, Max blue, Pro gold, Team green, Enterprise violet, unknown grey. All 7 of your saved accounts report Ultra (Max 20x), so they should all stay <b>orange</b> and say <b>Ultra</b>. "
           "ahmed@pixelventures.ai has had no subscription since 2026-11-16 and should show a grey <b>No plan</b> chip."),
  "why": "A glance at the card should tell you the plan, and a lapsed account should not look like a working Ultra one.",
  "steps": ["Open the popover and look at the plan chip on each account card.",
            "Your Ultra accounts: an orange chip that says <b>Ultra</b>.",
            "<b>ahmed@pixelventures.ai:</b> a grey chip that says <b>No plan</b>, and no Re-sign button on its card.",
            "Look in both light and dark if you like (System Settings → Appearance); both should be easy to read."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Look at the cards once",
  "options": [{"label": "A. Passed: Ultra accounts orange, ahmed@pixelventures.ai grey No plan", "note": "Pick this only if both were true."},
              {"label": "B. Something looks wrong", "note": "Say which account, what the chip said and what color it was."}, SKIP],
 },
 {
  "id": "limit-resets", "asked": ASKED,
  "title": "Does Limit resets open claude.ai as beeco1@pixelventures.ai, and show its resets?",
  "short": "Limit resets window, beeco1",
  "what": ("Each card has a <b>Limit resets</b> button. It opens that account's own claude.ai usage page in a PixelSwitch window with its own saved claude.ai sign-in. You sign in once per account. "
           "You decide yourself whether to click claude.ai's own reset button; PixelSwitch never uses a reset, and neither do I."
           + signin_pic +
           "The window has <b>Reload</b>, <b>Open in browser</b>, <b>Paste sign-in link</b> and <b>Sign out of claude.ai here</b>. While you are signed out, a bar says <b>Sign in as &lt;the account's email&gt;</b>. If the page fails to load, a banner offers Reload."),
  "why": ("Anthropic's server shows reset offers only to claude.ai and Claude Code, not to PixelSwitch, so this window is claude.ai itself, signed in as the right account. "
          "<b>Unproven until you try it:</b> whether <b>Continue with Google</b> works inside this window (if Google refuses an embedded window, the emailed sign-in link pasted into <b>Paste sign-in link</b> is the way; we do not disguise the window), "
          "and whether the wrong-account warning ever fires (the usage page may not show the account's email). The picture above shows the signed-out state; the picture of the wrong-account bar in the evidence folder is staged (both bars at once, which the real app never shows)."),
  "steps": ["On the <b>beeco1@pixelventures.ai</b> card, click <b>Limit resets</b>.",
            "A window titled <b>beeco1@pixelventures.ai · claude.ai</b> opens on claude.ai's sign-in page, with a bar that says <b>Sign in as beeco1@pixelventures.ai</b>.",
            "Try <b>Continue with Google</b> first and pick beeco1@pixelventures.ai.",
            "If Google refuses, use claude.ai's email option instead: enter the address, wait for claude.ai's email, copy its sign-in link, click <b>Paste sign-in link</b> in the window, paste it and confirm.",
            "When you are signed in, the usage page shows a <b>Limit resets</b> section with the account's full reset and its Fable reset.",
            "Whether to click one of claude.ai's reset buttons is your call. Do not click one just to test; if you do, tell me."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Sign in as beeco1 and look at the Limit resets section",
  "options": [{"label": "A. Passed: signed in, and the Limit resets section shows the full reset and the Fable reset", "note": "Say in the box whether you signed in with Google or the pasted link."},
              {"label": "B. The window opened but I could not sign in", "note": "Say which step failed and what the window said."},
              {"label": "C. Signed in, but the Limit resets section is missing or wrong", "note": "Say what the page showed."}],
 },
 {
  "id": "stays-signed-in", "asked": ASKED,
  "title": "After closing and reopening the window, are you still signed in?",
  "short": "Still signed in after reopening",
  "what": "The claude.ai sign-in is saved per account, so closing the window and clicking Limit resets again should not ask you to sign in again.",
  "why": "If it asked every time, the button would be too annoying to use.",
  "steps": ["After check 4, close the Limit resets window.", "Click <b>Limit resets</b> on the beeco1@pixelventures.ai card again.",
            "It should open straight on the usage page, with no sign-in bar.",
            "Optional: quit and reopen PixelSwitch, then try once more."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Close it, reopen it, look",
  "options": [{"label": "A. Passed: still signed in", "note": "Pick this only if no sign-in was asked for."},
              {"label": "B. It asked me to sign in again", "note": "Say whether that was after closing the window or after quitting PixelSwitch."}, SKIP],
 },
 {
  "id": "other-accounts", "asked": ASKED,
  "title": "Sign in the remaining accounts once each",
  "short": "Sign in the other accounts",
  "what": "Each account has its own claude.ai sign-in. Doing it once for each means Limit resets works from any card afterwards.",
  "why": "This also tells us whether the window behaves the same for every account, including personal Gmail ones.",
  "steps": ["For each of your other accounts, click <b>Limit resets</b> on its card and sign in as in check 4.",
            "The title and the <b>Sign in as</b> bar must name the account whose card you clicked. If a window ever shows a different account, say so.",
            "You do not have to do them all today. Say in the box which ones you did.",
            "ahmed@pixelventures.ai has no subscription, so its usage page may show nothing useful; that is expected."],
  "nothing": NOTHING, "nothingShort": NOTHING_S,
  "recommend": "Do them whenever it suits you",
  "options": [{"label": "A. Passed: the accounts I tried all worked", "note": "List them in the box below."},
              {"label": "B. One or more failed", "note": "Say which account and what went wrong."}, SKIP],
 },
 {
  "id": "google", "asked": ASKED,
  "title": "Did Continue with Google work inside the window?",
  "short": "Google sign-in in the window",
  "what": "This is the one fact I cannot learn without you. If Google refuses to sign in inside PixelSwitch's window, the emailed-link route is the way, and I will say so on the button's help text in the next version.",
  "why": "We do not disguise the window to get past Google. The honest outcome is either that it works or that the emailed link is the route.",
  "steps": ["Think back to check 4: what happened when you clicked <b>Continue with Google</b> in the window?"],
  "nothing": "Counted as not answered. The next version keeps both routes.",
  "nothingShort": "Counted as not answered; both routes stay.",
  "recommend": "Tell me what Google did",
  "options": [{"label": "A. Google worked inside the window", "note": ""},
              {"label": "B. Google refused or showed an error; I used the emailed link", "note": "Copy the error words into the box below if you can."},
              {"label": "C. I did not try Google", "note": "Fine."}],
 },
]

page = {
 "key": "pixelswitch-hand-checks-2-2-2026-11-19",
 "heading": "PIXELSWITCH: HAND CHECKS FOR 2.2 (2026-11-19)",
 "eyebrow": "PixelSwitch · hand checks · version 2.2 · 2026-11-19",
 "title": "Hand checks for 2.2: Re-sign, plan chips, Limit resets",
 "lede": [
  "<strong>Nothing on this page blocks the work.</strong> 2.2 is built, reviewed and released first and checked by you afterwards, as with every release since 1.2. "
  "Seven short checks: the version, the Re-sign button, the plan chips, and the new <b>Limit resets</b> window (sign in once per account).",
  "<b>PixelSwitch never uses a reset.</b> The Limit resets window is claude.ai's own page; clicking a reset there is only ever your decision.",
  "<b>About the pictures:</b> the one picture shows the window signed out with an example account, not yours."
 ],
 "more": [
  "<b>What is in 2.2.</b> (1) A Re-sign button on a main-window card whose sign-in expired. (2) One chip color per plan, and a grey No plan chip on a lapsed account. "
  "(3) A Limit resets button on each card that opens that account's own claude.ai usage page in a PixelSwitch window with its own saved sign-in. "
  "(4) The footer's Updated N min ago shows whole minutes and is never cut off, and Double-click to switch and Switching… are translated.",
  "<b>Not in 2.2:</b> the Linux version is unchanged. A direct reset call from PixelSwitch was deliberately not built: Anthropic offers resets only to claude.ai and Claude Code."
 ],
 "moreTitle": "What is in 2.2 (you do not need to read this)",
 "questions": cards,
}
t = TEMPLATE.read_text()
js = "var PAGE = " + json.dumps(page, ensure_ascii=False, indent=2) + ";\n\n"
start = t.index("var PAGE = {")
end = t.index("/* ================================== the page, rendered")
t = t[:start] + js + t[end:]
t = t.replace("<title>PROJECT: your decisions, round N</title>", "<title>PixelSwitch: hand checks for 2.2</title>")
OUT.write_text(t)
print("page written", len(cards), "cards ->", OUT)
