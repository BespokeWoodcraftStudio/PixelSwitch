# Evidence scripts for the 2026-10-16 usage 429 investigation

Plain Python 3, no packages to install. They read the PixelSwitch app log and its two rotated copies
(`~/Library/Logs/PixelSwitch-app.log`, `.log.1`, `.log.2`) and never call any Anthropic endpoint.

The logs were captured on 2026-10-06 23:22Z to 2026-10-16 14:08Z. PixelSwitch rotates its log, so on a later date the early
lines are gone and the numbers in the write-up (`../../2026-10-16-usage-429.md`) can no longer be reproduced exactly.

Account ids and emails near the top of `syn/parse.py` and `adv/p.py` are the founder's five accounts
(PV ahmed@pixelventures.ai, GM ahamade@gmail.com, BWS ahmed@bespokewoodcraftstudio.com, cPV claude@pixelventures.ai,
cBWS claude@bespokewoodcraftstudio.com), so the output reads by short name.

The two files these scripts write (`ev.json`, `d.pkl`) hold account data, so they are git-ignored here. Regenerate them.

Run each script from its own folder, because they use relative paths.

## `syn/` (the first diagnosis)

- `parse.py <out.json>`: reads the three logs and writes one event per usage request, profile lookup, refresh, switch and
  reconcile line, each tagged with its account and (for a usage request) its HTTP status. Run it first:
  `cd syn && python3 parse.py ev.json`. The `adv2/` scripts read `../syn/ev.json`.

## `adv/` (the first skeptic: refuting RC2 and finding the round-robin speed-up)

- `p.py`: parses the logs into every request with its status and latency and pickles it to `d.pkl`
  (`cd adv && python3 p.py`). The `q*.py` scripts read `d.pkl`.
- `q1.py`: latency by outcome (429 median 24 ms, 401 114 ms, 200 346 ms), then every 429 with the 200 and the 401s before it
  on the same account and the profile 401s in the previous hour.
- `q2.py`: for the two dead backups (PV, GM), the most 401s in any 60 minutes and any 4 hours, before and after
  2026-10-16 06:00Z. This is what shows "up to 4 per hour" was a closed-interval count and the old cadence was a strict 20 minutes.
- `q3.py`: the same accounts' failing requests with trailing 60, 90 and 120 minute counts and the span of the last 4 and 5.
  Before the 06:53Z auto-switch no four failures fell inside 60 minutes; after it they did. This is what refuted "the server got stricter".
- `q4.py`: the morning of 2026-10-16 rebuilt one polling cycle at a time (the active account, which accounts were parked,
  who was asked). It shows the auto-switch and the round-robin shrinking as accounts were parked.
- `q5.py`: profile 401 totals, and for each park the first request after it with the number of profile 401s in the hour before.
- `q6.py`: what the next requests did after each "Delegated refresh completed" line (13 runs, 0 recoveries).
- `q7.py`: for every usage request, the count of failed calls (usage 401s plus profile 401s) in the previous 30, 60 and 90 minutes,
  tabulated against the answer (200, 401, 429).

## `adv2/` (the second skeptic: fitting one rule to the whole log)

- `rc2.py`: for PV and GM, the most 401s before a request that was not a 429, and the gaps between requests per day.
- `fit.py`, `fit2.py`: try a token bucket and a sliding window with different sizes against every 401 and 429 of the two dead backups.
- `fit3.py`, `fit4.py`: a clock-aligned one-hour estimate of failed calls per account; `fit4.py` sweeps the threshold and needs
  `fit3.py` in the same folder.
- `fitall.py`: the same rule over all five accounts, with and without counting profile 401s. With them it predicts all 49 429s.
- `list16.py`: lists the usage, profile, switch and reconcile lines from 2026-10-15 19:40Z onward, for reading the
  founder's re-authentication and switch attempts in order.
