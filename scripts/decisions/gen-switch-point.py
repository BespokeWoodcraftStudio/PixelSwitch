# Builds docs/decisions/pixelswitch-switch-point-2026-09-29.html from the founder
# template (~/.claude/templates/founder-questions-template.html), changing only
# PAGE and <title>. Render: node scripts/decisions/render-decision-page.cjs <file> <out-dir> <first-id>
import json, pathlib
REPO = pathlib.Path(__file__).resolve().parents[2]
t = open('/Users/ahamade/.claude/templates/founder-questions-template.html').read()
page = {
 "key": "pixelswitch-switch-point-2026-09-29",
 "heading": "PIXELSWITCH: WHEN AUTO-SWITCH MOVES YOU (2026-09-29)",
 "eyebrow": "PixelSwitch · one question · 2026-09-29",
 "title": "Should auto-switch always move you at your number?",
 "lede": "<strong>Nothing is blocked: 1.7 is released and works either way.</strong> You asked: <i>if I set my switch number to 98% or 99%, it should switch on that number, shouldn't it?</i> It starts looking at your number, but it only moves you if another account has enough room, and today \"enough\" means 10 points. This is whether to change that.",
 "questions": [{
  "id": "switch-point",
  "title": "Switch at your number to any account with room",
  "what": "Your threshold (98%) is when PixelSwitch <b>starts looking</b>. Usage is read every 5 minutes, so it notices somewhere between 98% and 100%. It then moves you only to an account at least <b>10 points under its own threshold</b>, which is 88% or less at your 98%. On the Beeco Mac this morning every other account was at 92–99% of its week, so at 98% and 99% there was nowhere to go. Since 1.7, once you reach 100% it takes any account at 97% or less. So today, on a busy week, the switch happens at 100% (session empty), not at 98%.",
  "why": "The 10-point gap stops auto-switch moving you to an account that is itself about to run out and then moving you again minutes later. But you can only ever leave an account at or over its threshold and go to one under it, and there is a 5-minute cooldown, so it can never bounce back and forth between two accounts. Dropping the gap means you switch at 98% as you expect. The cost: on a week when every account is nearly full, you may switch every hour or so, each time to an account with only a few points left.",
  "nothing": "Stays as 1.7: at 98% it moves you only to an account at 88% or less; otherwise it waits until you hit 100% and then takes any account at 97% or less.",
  "recommend": "Switch at your number to any account with room",
  "options": [
   {"label": "Switch at my number to any account with at least a point of room", "note": "At 98% it moves you to the account with the most room, even one at 97%. The 10-point gap goes. Released as 1.8, tests first."},
   {"label": "Switch at my number, but keep a smaller gap (3 points)", "note": "At 98% it takes an account at 95% or less; nearer-full accounts wait until you hit 100%, as in 1.7."},
   {"label": "Keep 1.7 as it is", "note": "Switch at 98% only to an account at 88% or less; otherwise at 100%."}
  ]
 }]
}
js = "var PAGE = " + json.dumps(page, ensure_ascii=False, indent=2) + ";\n\n"
start = t.index("var PAGE = {"); end = t.index("/* ================================== the page, rendered")
t = t[:start] + js + t[end:]
t = t.replace("<title>PROJECT: your decisions, round N</title>", "<title>PixelSwitch: when auto-switch moves you</title>")
(REPO / "docs/decisions/pixelswitch-switch-point-2026-09-29.html").write_text(t)
print("written")
