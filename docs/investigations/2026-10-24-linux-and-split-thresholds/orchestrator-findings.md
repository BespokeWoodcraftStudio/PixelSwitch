# Findings the orchestrator made outside the review JSONs (2.0)

Each is assigned to a fix lane; a cold session checks it is closed before release.

| # | Found | Where | Problem | Assigned |
|---|---|---|---|---|
| O1 | CI run 40160978617, job "Linux tests (ubuntu-24.04)" | `linux/pixelswitch/dirlock.py`, `linux/tests/test_credtools_dirlock.py:165,188` | `test_release_leaves_a_lock_that_was_replaced` and `test_check_enforces_the_hold_limit_and_detects_a_taken_lock` fail on Ubuntu 24.04 (pass on 26.04 and macOS). A lock directory removed and re-made by someone else is not recognised as replaced, most likely because the check relies on the directory's inode, which ext4 reuses at once. Real effect: PixelSwitch could remove Claude Code's own refresh lock on release after Claude Code took it over. Fix: identify our lock by the holder record we wrote (pid, process start, a random nonce) and its birth time, not by inode alone. | Linux follow-up lane FL3 (dirlock.py belongs to FL1 while it runs) |
| O2 | The CI render `evidence/linux-settings-accounts-light.png` | `linux/pixelswitch/gui/` (Accounts page help line) | An account that follows split defaults (session 90, weekly 50) shows the equal-windows sentence "moves you off this account at 90%, and moves you to it only while it is at 89% or less" instead of the two-window sentence. | FL3 (gui/ is lane LC's) |
| O3 | Lane LC's laptop scratch run | `Tests/integration/remote-control.sh` check 2 | Reads `machine.todayCost`, which Linux deliberately omits (D15), so the script fails on Linux. Skip that check when `platform` is `linux`. | Mac lane FE (the script is lane MB's file) |
