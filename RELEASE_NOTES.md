## PixelSwitch 1.2

### New

- **Auto-switch rules you control.** Settings → General → Auto-switch now has:
  - a **Default threshold** (50–100%);
  - **Choose the next account by**: **Most room left** (the most headroom under each account's own threshold), **My order**, or **Resets soonest**;
  - with Resets soonest, **Switch early to use quota before it resets**: an account whose weekly limit resets within your chosen number of hours (1–72) is used up first. It has its own on/off switch.
- **A new Accounts tab in Settings.**
  - Drag accounts into the priority order you want; the popover follows it.
  - Give any account its own switch threshold, or leave it on the default.
  - See when each account's weekly limit resets.
  - Add the current account or sign in a new one from here.
- **Sign-in links, and nothing opens by itself.** Signing in or re-signing an account opens a small **Sign in to Claude** window with **Open in default browser**, **Open in** (pick any installed browser) and **Copy link**, so you can sign in from the browser that holds the right Claude account. **Signing in on another device?** gives a link for a phone or another computer, plus a box for the code it shows.
- **Remote control from the command line or an AI.** Settings → Claude CLI → **Command line & AI** → **Install** puts a `pixelswitch` command at `~/.local/bin/pixelswitch`.
  - It can show accounts and usage, switch, add, sign in, reorder, set thresholds and labels, change settings, and watch live events.
  - From another Mac, run it over SSH. Or add the MCP snippet shown there to Claude on that Mac: Claude can then check your usage and switch accounts for you.
  - Only your user on this Mac can reach it. Nothing listens on the network, and no login token is ever handed out.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.1...v1.2
