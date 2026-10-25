# Findings the orchestrator made outside the review JSONs (2.0)

Each is assigned to a fix lane; a cold session checks it is closed before release.

| # | Found | Where | Problem | Assigned |
|---|---|---|---|---|
| O1 | CI run 40160978617, job "Linux tests (ubuntu-24.04)" | `linux/pixelswitch/dirlock.py`, `linux/tests/test_credtools_dirlock.py:165,188` | `test_release_leaves_a_lock_that_was_replaced` and `test_check_enforces_the_hold_limit_and_detects_a_taken_lock` fail on Ubuntu 24.04 (pass on 26.04 and macOS). A lock directory removed and re-made by someone else is not recognised as replaced, most likely because the check relies on the directory's inode, which ext4 reuses at once. Real effect: PixelSwitch could remove Claude Code's own refresh lock on release after Claude Code took it over. Fix: identify our lock by the holder record we wrote (pid, process start, a random nonce) and its birth time, not by inode alone. | Linux follow-up lane FL3 (dirlock.py belongs to FL1 while it runs) |
| O2 | The CI render `evidence/linux-settings-accounts-light.png` | `linux/pixelswitch/gui/` (Accounts page help line) | An account that follows split defaults (session 90, weekly 50) shows the equal-windows sentence "moves you off this account at 90%, and moves you to it only while it is at 89% or less" instead of the two-window sentence. | FL3 (gui/ is lane LC's) |
| O3 | Lane LC's laptop scratch run | `Tests/integration/remote-control.sh` check 2 | Reads `machine.todayCost`, which Linux deliberately omits (D15), so the script fails on Linux. Skip that check when `platform` is `linux`. | Mac lane FE (the script is lane MB's file) |

## O1 update (CI run 40162811377, after lane FL1's birth-time change)

Still failing on Ubuntu 24.04 (`test_credtools_dirlock.py:165` and `:192`), passing on 26.04. Root cause, read in
`linux/pixelswitch/dirlock.py`: `_is_ours` compares the birth time with a 2 ms tolerance (`abs(birth - held.birth_ms) < 2.0`)
and, where no birth time exists, the mtime with a 2 ms tolerance (`abs(status.st_mtime - held.last_touch) < 0.002`). A
directory removed and re-made within 2 ms passes both (ext4 also reuses the inode at once), which a fast runner does.
Fix for lane FL4: keep `birth_time_ms` for the owner record Claude Code reads, but decide ownership on EXACT values:
the statx birth time in whole nanoseconds (`seconds * 10**9 + nanos`, compared with `==`), and in the fallback an mtime
PixelSwitch sets with `os.utime(path, ns=(t, t))` where `t` carries a random sub-millisecond nanosecond part, compared
with `st_mtime_ns ==`. Add a deterministic test that reuses the inode number and lands inside the old 2 ms window (by
patching `os.lstat`/`birth_time` to return the same inode and a birth time 1 ms away) and must report "not ours".

## O4: a lane changed the founder's Python (2026-10-24 22:03 PDT)

Lane FL3 ran `brew install gnu-tar gtk4 libadwaita pygobject3` on the founder's Mac. pygobject3 pulled in
`python@3.15`, and Homebrew linked it as the default `python3` (it had been 3.14.7), so tools installed for 3.14
(graphify) would not have been found. The orchestrator restored the default with `brew unlink python@3.14 && brew link
python@3.14` (after `brew unlink python@3.15`); `python3` is 3.14.7 again and `import graphify` works. python@3.15,
gtk4, libadwaita, pygobject3 and gnu-tar (as `gtar`) remain installed and change no default. The lane plan now forbids
installing anything on the founder's machines.

## Closure (2026-10-25)

| # | Closed by | Proof |
|---|---|---|
| O1 | Lane FL4 (d85ba1c): dirlock ownership on exact values (statx birth time in ns, PixelSwitch's own random-ns mtime read back) | CI run 40165732719 and the v2.0 release run 40167290828: Linux tests green on Ubuntu 24.04 and 26.04; deterministic same-inode tests |
| O2 | Lane FL3 (6325b7c): `model.limit_help` judges a default-following account by the snapshot's defaults | `test_gui_model.py`; confirmed in the final Opus check |
| O3 | Lane FE (03478e0): `remote-control.sh` skips `machine.todayCost` on Linux | `bash -n`; the Linux scratch runs |
| O4 | Orchestrator: default `python3` restored to 3.14.7; lane rule added | `python3 --version`, `import graphify`; lanes plan "Never install" rule |
