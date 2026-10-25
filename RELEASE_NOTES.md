## PixelSwitch 2.0

### New

- **Separate limits for the 5-hour session and the week.** Settings → General now has two defaults, "Session (5-hour) default" and "Weekly default" (each 50% to 100%), and every account can have its own Session and Weekly limit (1% to 100%) in Settings → Accounts. For example, session 90% and weekly 50%: PixelSwitch moves you off an account when it reaches 90% of its 5-hour session or 50% of its week, and moves you to an account only while it is under both.
  - When no account under its weekly limit can take you, the session limit decides, so you are never left at 100% of a session while another account still has room. With equal numbers for both (as before 2.0) nothing changes.
  - Fable uses the weekly limit.
  - An account's limit from 1.9 applies to both windows until you change one of them.
- **Priority numbers.** Give any account a priority from 1 to 99 in Settings → Accounts (shown as P1, P2, …), or with `pixelswitch accounts priority <account> <number>`. Lower numbers are tried first, and gaps are fine (P1 and P3 with no P2). With "Choose the next account by: Priority (lowest number first)", auto-switch goes to the lowest-numbered account that has room, so an account that has freed up again is used before the ones below it. Every account list follows the numbers. Dragging a row still works; it renumbers every account from 1.
  - "Priority" used to be called "My order". If you choose by "Most room left" (the default), the numbers only break ties; Settings → Accounts says so and has a **Use priority** button.
- **PixelSwitch for Linux (Ubuntu 24.04 and 26.04).** The same accounts, switching, usage readings and auto-switch rules, with a top-bar icon, a settings window and the same `pixelswitch` command line and MCP tools. It installs for your user only, without sudo, and updates itself. See "Linux" below.
- **Command line and AI tools.** `pixelswitch accounts threshold <account> --session 90 --weekly 50`, `pixelswitch accounts priority <account> <number>`, a Priority column in `pixelswitch accounts`, and two new MCP tools, `set_account_thresholds` and `set_account_priority`. Older commands and scripts keep working.

### Fixed

- **A blank login can no longer overwrite a good saved one.** When Claude Code gives up on a login it blanks it on disk; PixelSwitch could then save that blank over the account's good saved login. Now a login without a usable token is never saved, never written into Claude Code, and the account says it needs signing in again.
- **Switching re-checks the login just before writing.** If Claude Code renewed the login in the moment before a switch, PixelSwitch saves the new one first instead of losing it.
- **Renewed logins keep an honest expiry date,** so Claude Code does not treat a renewed saved login as signed out.
- **Removing the account you are using now switches Claude Code to another account.** Before, Claude Code stayed on the removed account. Removing your last account no longer brings it back by itself.

### Linux

LINUX_INSTALL_PLACEHOLDER

On a computer where other tools use the same Claude Code login (for example the CabDesign farm's Claude jobs on the Ubuntu laptop), switching accounts, by hand or automatically, changes the account those tools use too. Auto-switch starts off on Linux; turn it on in Settings → General when you want it.

The new messages are in English, German, French, Japanese and Simplified Chinese on the Mac; the Linux version is in English.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.9...v2.0
