# The laptop's CabDesign farm and Claude Code's login (spec 5.7 item 8)

Read 2026-10-24 by the orchestrating session, read-only over SSH (`ahmed@ahmed-aorus-15p-xd.tail19d9f2.ts.net`),
structure only; no secret was printed or copied.

## What was found

- `~/cabdesign-farm-account.sh` (written 2026-10-19) is a one-time sudo setup that makes a separate `cabfarm` system
  account so farm jobs cannot reach the founder's files. It **does not touch Claude Code's login** ("It does not touch
  SSH, the firewall, power settings, Tailscale or Claude"). The `cabfarm` account exists (uid 997) and
  `/etc/sudoers.d/cabfarm` is installed.
- **The farm jobs running on 2026-10-24 ran as `ahmed`**, not `cabfarm`: systemd user scopes `farm-laptop-*` start
  `~/.cabdesign-farm/jobs/<job>/farm-job.sh` under the founder's user manager.
- `farm-job.sh` has two modes: `exec` ("with NO Claude") and `claude` (slice F5, card P3170): "the fixed command is a
  `claude -p` line". A `claude` job runs as `ahmed` with `HOME=/home/ahmed`, so it reads
  `~/.claude/.credentials.json`, the file PixelSwitch switches.
- The Claude desktop app (`com.anthropic.Claude`) was also running; it keeps its own login, separate from Claude Code's.

## What it means for PixelSwitch on that laptop

- A switch (by hand or by auto-switch) changes the account that farm `claude` jobs use from their next request on, the
  same way it changes the account of any Claude Code session on that machine. A job mid-run continues on the new login.
- Auto-switch can therefore help the farm (moving it off an account at its limit), but it also moves farm work onto
  accounts the founder may want to keep for himself (his Manual only and priority settings apply).
- Hence, per spec 5.7: the real install leaves auto-switch off; `linux/README.md`, the 2.0 release notes and the Linux
  hand-check card on the founder page each say this in one sentence, and the card recommends trying switching while the
  farm is idle.
- If the farm later moves its jobs to the `cabfarm` account (its own HOME and its own `~/.claude`), PixelSwitch running
  as `ahmed` no longer affects them.
