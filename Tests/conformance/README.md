# Auto-switch conformance vectors

`autoswitch-vectors.json` is the shared contract between the Swift engine (macOS) and the Python engine (Linux) in
PixelSwitch 2.0. It is the executable form of sections 2, 3 and 6 of
`docs/superpowers/specs/2026-10-24-2.0-spec.md` (revision 3). Both engines must produce, for every case, exactly the
`expect` block. The expected values were derived by hand from the spec and from `PixelSwitch/Services/AutoSwitchEngine.swift`
(the 1.9 engine, whose unchanged rules the spec extends), then checked against an independent scratch reference
implementation that is not committed.

**Nobody edits this file after lane V.** A case you believe is wrong is reported to the orchestrator with your reasoning;
you never change the file to make a harness pass.

## File shape

```
{ "version": 1, "hysteresisDefault": 1, "drainMinimumRoom": 1, "cases": [ case, ... ] }
```

`hysteresisDefault` and `drainMinimumRoom` are the engine constants (`AutoSwitchEngine.hysteresis`, `.drainMinimumRoom`).
A case's own `settings.hysteresis` overrides the first when present.

### A case

| Field | Meaning |
|---|---|
| `name` | Says what the case proves, so a failure explains itself. Unique. |
| `now` | The clock, ISO 8601 UTC with a `Z`, whole seconds. Every `resets_at` is judged against it. |
| `settings.strategy` | `mostRoom`, `myOrder` (shown as "Priority") or `resetsSoonest`. |
| `settings.watchFable` | The Fable setting. |
| `settings.drainEarly` | "Switch early to use quota before it resets". |
| `settings.drainWithinHours` | The drain window in hours; the engine's `drainWithin` is this times 3600 seconds. |
| `settings.hysteresis` | Points of room a threshold-triggered target needs (`ceiling(t, room)`). |
| `settings.defaults` | The RAW stored global values, `base` (`autoSwitchThreshold`), `session` and `weekly` (the two global window keys). Absent means unset. Spec 2.2 applies: a key counts as set only when present, numeric and not 0; `null` and `0` follow the base; results clamp to 50-100. |
| `accounts[]` | The accounts IN STORED ORDER (before sorting), see below. |
| `active` | The id of the active account. |
| `activeSampledThisCycle` | Whether the active account's reading was taken by the cycle that is asking. `true`: a reading with no `resets_at` is trusted. `false`: a reading with no `resets_at` is dropped on the ACTIVE side (strict expiry); candidates are always lenient. A window whose `resets_at` is earlier than `now` is dropped on both sides, whatever this flag says. A `resets_at` equal to `now` is still live. |
| `expect.order` | Account ids after `sortedByPriority`. |
| `expect.rules` | For every account: `session`, `weekly` (resolved rule values, 0 for Manual only), `manualOnly`, `leaveSession`, `leaveWeekly` (spec 2.2). These are always the STRICT values; the relaxed pass of rule 9 is not shown here. |
| `expect.plan` | `null` (stay put) or `{limit, trigger, window?, relaxed, targets}`. |

### An account

| Field | Meaning |
|---|---|
| `id` | A short label. The Swift harness maps each to a fixed UUID. |
| `priority` | The stored priority number, 1-99. Absent means none. |
| `prioritySetOrder` | How recently the number was set, used only to break a tie between EQUAL numbers: a HIGHER value means set MORE recently, so the account with the higher value goes first. Absent means never set, which sorts after every account of that number that was set. Equal numbers that were never set keep the stored order. The harness feeds it to `sortedByPriority(_:lastSet:)` as the `lastSet` argument. Unnumbered accounts carry no `prioritySetOrder`. |
| `switchThreshold` | The both-windows value (0 = Manual only). Raw: the engine rounds half away from zero and clamps to 0-100, so `-5` and `0.4` mean Manual only here, and `100.4` means 100. |
| `sessionThreshold`, `weeklyThreshold` | The per-window values. Raw: `window(v)` rounds half away from zero; a result of 0 or less means "follow the next layer" (so `0`, `-5`, `0.4`, `-0.5` follow), above 100 means 100 (`100.4`). Manual only on `switchThreshold` wins over both. |
| `switchable` | Whether the account has a usable saved login (the engine's `isSwitchable`). |
| `unusable` | Whether the account has no subscription (the engine's `isUnusable`). |
| `usage` | Optional. Absent means no sample at all; `{}` means a sample with no windows. Keys: `five_hour` (session), `seven_day` (weekly), `fable` (the weekly Fable allowance; the harness maps it to the `limits` list entry that `modelWeeklyLimit(named: "Fable")` finds). Each window is `{"utilization": number|null, "resets_at": ISO string, optional}`. `utilization` is a 0-100 percentage; `null` means no reading. A missing `resets_at` means the API did not say. |

## The harness procedure (spec section 6)

For each case, in this order:

1. Sort `accounts` with `sortedByPriority`, passing `prioritySetOrder` as `lastSet`. Check the resulting ids equal
   `expect.order`.
2. The candidates are every account except `active`, in that sorted order.
3. Apply spec 2.2 to the RAW stored values (the account fields and `settings.defaults`) for every account, including the
   active one. Check `expect.rules` field by field.
4. Run the plan with those resolved rules and the settings, `now` as the clock and `activeSampledThisCycle` as given.
   The plan is rule 9's `strict ?? relaxed`: compute the strict plan; only when it is nil and overflow holds (the active
   account has no subscription, or has reached its strict window limit under rule 1's expiry rule) compute the relaxed
   plan. Check `expect.plan`: `null`, or `limit`, `trigger`, `relaxed`, and `targets` (every eligible candidate in ranked
   order, best first, not only the first). `window` (`session` or `weekly`) is present exactly when a window rule fired
   and the limit is `windows` with trigger `threshold`; it is absent for `drainEarly`, `noSubscription` and the `fable`
   limit, and the harness checks it is absent there.

Compare numbers as numbers (`90` equals `90.0`). Ids are compared as exact strings.

## What the cases cover

Equal thresholds that reproduce 1.9 decisions (including the 98% Beeco Mac setup); the founder's 90 / 50 example in both
directions; every layer of the 2.2 resolution chain and the results of the 2.1 split; Manual only active and as a
candidate; Fable on the weekly rule, `keepsFable` with no Fable reading and strict Fable expiry; early drain and
no-subscription with split values; weekly overflow (rule 9) cases that must MOVE and cases that must STAY, including a
two-cycle sequence showing no return within the cooldown; expired windows on both sides, `activeSampledThisCycle` true and
false; a missing window; no reading at all; ranking ties under all three strategies (`resetsSoonest` compares whole
minutes); priority gaps, ties and unnumbered accounts; and the raw values 0, -5, 0.4 and 100.4.

Spec points the vectors pin that a reader may not expect:

- `keepsFable` ranks `mostRoom` even when the Fable setting is off, exactly as `AutoSwitchEngine.rankedTargets` does today.
- A candidate with `utilization: null` in every window has no reading and is not eligible; a candidate with only one
  window is judged on that window alone.
- A drain target needs a weekly reset strictly earlier than the active account's, compared as instants, but drain
  targets are then ranked by whole minutes.
- The relaxed pass changes the weekly rule of every account and the weekly default to `max(weekly, session)`; Manual only
  stays Manual only and still leaves at the (raised) defaults.
