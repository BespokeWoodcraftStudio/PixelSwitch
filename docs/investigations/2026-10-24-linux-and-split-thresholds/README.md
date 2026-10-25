# 2.0 investigation: separate session/weekly limits, priority numbers, Linux

Everything behind PixelSwitch 2.0 (released 2026-10-25). The contract is
`docs/superpowers/specs/2026-10-24-2.0-spec.md`; the lanes are in `docs/superpowers/plans/2026-10-24-2.0-lanes.md`;
the story is worklog WL-2026-10-24-001, WL-2026-10-24-002 and WL-2026-10-25-001.

| File | What it is |
|---|---|
| `recon/*.md` | Seven read-only reports the spec was written from: credentials, usage (and the 429 guard), autoswitch, control (protocol, CLI, MCP), signin, release (CI, signing, appendix A Ed25519 verifier), laptop (the Ubuntu machine) |
| `spec-review-2026-10-24.json` | Opus review of spec revision 1 (three lenses: engine/protocol, Linux login safety, scope) |
| `spec-recheck-2026-10-24.json` | Opus recheck of revision 2, with the worked weekly-overflow cases behind rule 9 |
| `review-ma-*`, `review-mb-*` | Opus reviews of the Mac lanes (engine and AppState; control, CLI, MCP, views, strings) |
| `review-la-*`, `review-lb-*`, `review-lc-*` | Opus reviews of the Linux lanes (pure core; daemon; CLI, MCP, tray, window, packaging, CI) |
| `rereview-fa-*` ... `rereview-fd-*`, `rereview-fl1-fl2-*`, `final-check-linux-*` | Re-reviews of every fix lane until no P0/P1 remained |
| `orchestrator-findings.md` | Findings the orchestrator made itself (O1 the Ubuntu 24.04 lock test, O2 a help line, O3 the test script on Linux, O4 a lane changing the default python3) and how each closed |
| `laptop-farm.md` | Why switching on the Ubuntu laptop also moves the CabDesign farm's Claude jobs |
| `evidence/` | Renders: Mac Settings rows and defaults (en, de, fr, ja, zh-Hans, light and dark), the Linux settings window from CI, the founder page at 1920x1080 |
