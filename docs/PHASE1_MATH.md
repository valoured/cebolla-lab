# Cebolla.live — Picking Math (Phase 1)

*Last updated: May 2026 — Phase 1 rollout*

---

## Philosophy

The model picks batters who are **likely to do the thing**, then checks if the **price isn't insulting**. Not the other way around.

This is a philosophical correction from v2. Previously, the model started by finding +EV plays (edges between projected probability and market odds) and ranked them. The problem: some of the best matchup reads in baseball have *negative* edge because the market already knows about them. Rob Refsnyder going .375 with 3 HR in 11 ABs against Jeffrey Springs is a real signal — but if the sportsbook prices Refsnyder accordingly, the "edge" disappears and the v2 model would never pick him.

Phase 1 flips this. **Matchup-first, EV-secondary.** Find the strongest skill+matchup read, then check the price.

The model is **honest about conviction**. If the best read on a given slate is a 27% projected HR with no real matchup signal, the model labels it as a Lottery pick (small stake) rather than dressing it up as "high confidence." Most slates produce a mix of Lock-tier reads (genuine 70%+ skill+matchup edges) and Lottery-tier dart throws. The system surfaces both honestly.

---

## How a Batter Becomes Eligible

Before a batter can be ranked, they pass an eligibility filter. **Two gates exist — pass either to proceed.**

### Hard Exclusion (applies first)

```
season_PA < 50  →  never eligible
```

This kills "cup of coffee" batters with 5-20 PA. The model needs enough data to know what a batter is.

### Gate A — Opportunity Gate (the default)

```
season_PA >= 120  AND  pitch_type_PA >= 20
```

The batter has a real season's worth of plate appearances AND they've seen the opposing pitcher's primary pitch type enough times to have a meaningful read on it.

`pitch_type_PA` is **family-summed** — if the pitcher's primary pitch is a 4-seam fastball, the gate counts the batter's PA across ALL fastballs (4SM + SI + CT). This recognizes that fastballs play similarly; a batter with 30 PA against sinkers has real read on how they handle fastballs even if they've only seen 5 four-seamers.

**Yordan Alvarez** clears Gate A trivially — he's a regular with 250+ season PA and has seen every pitch type. Most regular MLB hitters pass Gate A.

### Gate B — Matchup Exception (the special case)

For batters who fail Gate A but have a **specific elite matchup**:

```
career AB vs today's pitcher >= 8
  AND
  (HR >= 2  OR  (AVG >= .300  AND  HR >= 1))      [the power condition]
  AND
  (season barrel% >= 8.0  OR  season xSLG >= .430)  [the corroborator]
```

All three required. The batter has faced this pitcher enough times (≥8 AB) to have a real history, has demonstrated power in that history (2 HRs or a .300 average with a HR), AND their season-long underlying skill profile supports it (8% barrel rate or .430 xSLG, both well above league average).

The corroborator is the safety check — it prevents a 3-for-8 fluke with 2 HRs against a single pitcher from elevating a slap hitter into a HR pick. Gate B requires both the matchup data AND the underlying skill to be there.

**Rob Refsnyder vs Jeffrey Springs** is the canonical Gate B case:

- 8 AB ✓
- 2 HR ✓ (power condition cleared)
- Season barrel% 10.34, xSLG .497 — both clear the corroborator ✓

He passes Gate B even though he might fail Gate A in some configurations (lower season PA, modest pitch-type sample).

### What's NOT a Gate

- **Edge / EV.** Edge does not gate eligibility. A batter can be eligible with negative edge — they just won't get the top stake tier.
- **Heat / hot streaks.** v2 used a "combined heat" signal that gated and ranked. Phase 1 removed this entirely from the picker. Heat data still gets computed (for the frontend), but the picker doesn't read it.
- **Lineup confirmation.** The picker assumes lineups are populated upstream. If a batter isn't in today's lineup, they won't have a projection row, so they won't be considered.

---

## The Primary Signal — How Batters Are Ranked

Eligible batters get scored by a single number called `primary_signal`, on a roughly 0–1 scale. Higher = better.

Primary signal is the **max** of three independently computed components:

### Component 1 — observed_vs_pitcher

```
hr / ab   (if career AB vs this pitcher >= 8)
```

The batter's HR rate per AB against THIS specific pitcher, from career history. Refsnyder's 2 HR in 8 AB vs Springs = **0.250**.

If the batter has no history against this pitcher (or <8 AB), this component is unavailable.

**Source label when this wins:** `bvp_observed`

### Component 2 — observed_vs_pitch_type

```
hr_pct / 100   (using the pitcher's primary pitch's HR rate)
```

How often the batter HRs against the pitch type the opposing pitcher will throw most. From season-long batted ball data, the `hr_pct` field tells us the batter's HR rate against that specific pitch.

**Reliability gate:** family-summed PA across this pitch's family must be ≥ 20. We use the specific pitch's HR rate but require enough overall family exposure to trust the sample.

If the pitch type isn't in the batter's `by_pitch_type` data OR family PA is too low, this component is unavailable.

**Source label when this wins:** `pitch_type_observed`

### Component 3 — recent_power_form

```
L7 xSLG / 2.0   (if L7 PA >= 10)
```

The batter's expected slugging percentage over the last 7 days, scaled by 2.0 to put it in the same 0–1 range as the other components.

xSLG is Statcast's "expected slugging" — it bakes in exit velocity, launch angle, and contact quality, and is the cleanest single power proxy available. Recent xSLG captures whether a batter is currently barreling the ball at an elite rate.

**Fallback:** if L7 has fewer than 10 PA OR no L7 row exists, fall back to L14 xSLG / 2.0 (any PA count, just needs xSLG present).

**Yordan Alvarez's L7 xSLG of 1.778** → primary_signal contribution = 1.778 / 2.0 = **0.889**. That's a massive recent_power_form signal — exactly the read v2 was missing.

**Source label when this wins:** `l7_power_form` (or `l14_power_form` if fallback used)

### Why max() and not weighted average?

A single elite signal should win. If a batter has a mediocre L7 form but their career numbers vs the specific pitcher are extraordinary, the matchup wins. If they have no matchup history but are currently slugging 1.7+, recent form wins. We don't want average-of-mediocre to outrank one-elite-read.

The source label tells you *which* signal drove the pick — if every pick today is `l7_power_form` and no `bvp_observed` fires, that's a slate where matchup data isn't engaging, useful diagnostic.

---

## How Primary Signal Becomes a Stake Tier (Advisory)

After ranking by primary_signal, the model assigns a suggested stake tier:

| primary_signal | tier         | stake          | meaning                                |
|---------------:|:-------------|:---------------|:---------------------------------------|
| ≥ 0.65         | **Lock**     | 2U  ($200)     | Top conviction. Real matchup edge.     |
| ≥ 0.50         | **Safe**     | 1U  ($100)     | Strong read. Solid builder.            |
| ≥ 0.30         | **Risky**    | 0.25U ($25)    | Decent signal. Worth a small play.     |
| ≥ 0.15         | **Lottery**  | 0.10U ($10)    | Modest read. Treat as lottery ticket.  |
| < 0.15         | **Donation** | 0.05U ($5)     | Weak read. Throwing money at it.       |

**Important: this is currently advisory only.** The frontend will display the tier label, but the actual stake recommendation isn't activated yet. Activation requires calibration — we need enough settled picks to confirm that "Lock" really hits ~70%, "Safe" really hits ~60%, etc. Until then, the tiers are display labels, not stake instructions.

The bankroll math (3–5U total daily spend, "Builder/Safe EV > total Lottery/Donation stake") is the framework. The tier output tells you which picks should get which stake. But verify the hit rates first.

---

## The EV Secondary Screen

After ranking by primary_signal, the EV (edge between projected probability and market odds) acts as a **secondary check that demotes stake tier, not a hard gate.**

| edge          | screen action  | what happens                                  |
|--------------:|:---------------|:----------------------------------------------|
| ≥ 0.03        | **full**       | Stake tier stays as-is                        |
| 0.0 to 0.03   | **drop**       | Stake tier demoted one step worse             |
| -0.10 to 0.0  | **warn_drop**  | Stake tier demoted AND warning flag set       |
| < -0.10       | **disqualify** | Pick rejected entirely                        |

The disqualify floor exists because if the market thinks a pick is *much* worse than the model does, the market is usually right — sportsbooks know things. Beyond -10% edge, the pick shouldn't ship even if the matchup is strong.

But for marginal negative edges (-0.05, -0.08), the model still surfaces the pick. It just labels it accurately: "matchup says Lock, but market disagrees; treat as Safe instead with a warning."

---

## How POD Picks Work

PODs (Plays of the Day) are the ONE highest-conviction pick per market for the day.

**Phase 1 unifies the anchor across markets.** The same batter who anchors the HR POD also anchors the HRR POD, when possible.

Process:

1. Pull all batters with HR or HRR projections for today's slate
2. Run eligibility + primary_signal for each batter (market-agnostic — both gates and the three components only depend on batter+pitcher data, not market)
3. Rank batters by primary_signal (max_edge as tiebreaker)
4. **HR POD** = top-ranked batter who has an HR projection passing EV screen
5. **HRR POD** = same batter, expressed in HRR market, IF they have an HRR projection passing EV screen. Otherwise, the next-best batter who does.

The same batter usually anchors both. A batter with strong matchup reads is strong for both markets — they're likely to HR AND likely to clear hits+runs+RBIs threshold. Same read, two expressions.

If today's top anchor has a Lock-tier primary_signal but the HR price is bad enough to disqualify (edge < -0.10), the HR POD falls to the next anchor with a passing HR projection. The HRR slot is independent — it walks the anchor list separately.

---

## How Card Picks Work

Cards are multi-leg parlays. Each leg is a batter+market pick that passes Phase 1 eligibility and EV screen.

### Multi-leg cards

1. Pull all projection rows across HR, HRR (lines 1.5/2.5), hits_yes, rbi_yes markets
2. Apply per-market probability floor (sanity floor — e.g., 1+ hits at 8% projected probability is a data issue, filtered out)
3. Run eligibility + primary_signal on the resulting pool
4. Sort qualified candidates by (primary_signal, edge) descending
5. Generate combinations (2-leg, 3-leg, 4-leg) — **no pool size cap**
6. Score each combo by `ev_per_dollar * 100 + sum(leg_primary_signal) * 5` (EV dominates, signal breaks ties)
7. Select top-scoring non-duplicate combos — **no final card count cap**
8. **Per-batter cap: 2 cards.** A batter can appear in at most 2 multi-leg cards across the day's output.

### Single-leg straights (per-game coverage)

After multi-leg cards are generated, run a coverage pass:

1. For each game in today's slate, find the best Phase 1 anchor (highest primary_signal)
2. If that anchor's `suggested_stake_tier` is **Lock or Safe** → emit a single-leg "straight pick" card
3. If no anchor in that game clears Lock/Safe → no straight from that game (Phase 1 is honest about conviction; not every game deserves a high-confidence single)
4. If the anchor is already at the 2-card cap from multi-leg appearances, the **straight wins** — one of their multi-leg cards is evicted to make room
5. The straight gets `tier = "single_leg"` and all standard Phase 1 metadata

**Result:** every game with at least one top-conviction read gets representation. A 15-game slate produces ~15 straights (if conviction supports it) + however many multi-leg cards the candidate pool produces.

A 6-game slate with weak overall conviction might produce 0 straights and 4 multi-leg cards. That's fine — the system is being honest that today's slate doesn't have Lock/Safe-tier reads anywhere.

---

## Worked Example — Jeremy Peña, May 28 2026

From an actual log:

```
Jeremy Peña (HOU vs TEX)  sig=0.275 [l7_power_form, gate B]
  HR POD:  @ +800   edge=0.033  ev=full   stake=lottery
  HRR POD: @ -105   edge=0.024  ev=drop   stake=donation
```

Breaking this down:

- **Gate B** — Peña passed via matchup exception (likely <120 season PA, but his BvP vs the Texas starter cleared the matchup threshold + his season barrel%/xSLG cleared the corroborator)
- **primary_signal = 0.275** — fairly weak. Lottery tier (≥0.15, <0.30).
- **Source: l7_power_form** — his L7 xSLG / 2.0 was the highest of the three components. Means his BvP HR rate was lower than 0.275 AND his vs-pitch-type HR rate was lower than 0.275.
- **HR market** — edge=+0.033 cleared the full floor (≥0.03), so the screen is "full." Stake stays at Lottery (his tier from signal alone).
- **HRR market** — same batter, but HRR edge is only +0.024 (between drop_floor=0.0 and full_floor=0.03), so screen is "drop." Stake demoted from Lottery → Donation.

**Interpretation:** Today's slate didn't have a strong read. Peña was the best available anchor with a 0.275 signal driven primarily by recent power form, not matchup specificity. The model honestly labels both his picks as Lottery/Donation. That's correct — the conviction matches the data.

A v2-era pick on the same slate would have surfaced Peña with `confidence_score=0.520` and labeled him B+ tier. Phase 1's downgrade to Lottery is the honesty correction.

---

## Where Thresholds Live

All thresholds are in the `model_thresholds` table in Supabase. They can be tuned without code deploys.

Key threshold rows with current values:

| key                                       | value  | purpose                                          |
|:------------------------------------------|-------:|:-------------------------------------------------|
| `eligibility_season_pa_hard_min`          | 50     | Hard exclusion                                   |
| `eligibility_gate_a_season_pa_min`        | 120    | Gate A regular threshold                         |
| `eligibility_gate_a_pitch_type_pa_min`    | 20     | Gate A pitch-family exposure                     |
| `eligibility_gate_b_bvp_ab_min`           | 8      | Gate B sample size                               |
| `eligibility_gate_b_bvp_hr_min`           | 2      | Gate B power (HR-only)                           |
| `eligibility_gate_b_bvp_avg_min`          | 0.300  | Gate B power (AVG path)                          |
| `eligibility_gate_b_bvp_hr_alt_min`       | 1      | Gate B power (AVG path's HR requirement)         |
| `eligibility_gate_b_barrel_pct_min`       | 8.0    | Gate B corroborator (barrel%)                    |
| `eligibility_gate_b_xslg_min`             | 0.430  | Gate B corroborator (xSLG)                       |
| `primary_bvp_ab_min`                      | 8      | Ranking signal #1 floor                          |
| `primary_pitch_type_pa_min`               | 20     | Ranking signal #2 family-PA floor                |
| `primary_l7_pa_min`                       | 10     | Ranking signal #3 L7 PA floor                    |
| `primary_l7_xslg_divisor`                 | 2.0    | xSLG scaling factor                              |
| `ev_edge_full_floor`                      | 0.03   | EV: full stake tier                              |
| `ev_edge_drop_floor`                      | 0.0    | EV: demote one tier                              |
| `ev_edge_warn_floor`                      | -0.10  | EV: demote + warn / disqualify boundary          |
| `stake_tier_lock_min`                     | 0.65   | Lock tier signal floor                           |
| `stake_tier_safe_min`                     | 0.50   | Safe tier signal floor                           |
| `stake_tier_risky_min`                    | 0.30   | Risky tier signal floor                          |
| `stake_tier_lottery_min`                  | 0.15   | Lottery / Donation boundary                      |

To tune:

```sql
UPDATE model_thresholds SET num_value = X WHERE key = 'foo';
```

The next cron pick run picks up the new value automatically.

---

## What's Coming (Phase 2+)

These are deferred — not in Phase 1:

- **Calibration audit** — once enough settled picks accumulate per tier, audit whether "Lock" actually hits ~70%. If yes, activate stake-mapping for real. If no, retune the tier breakpoints.
- **Stake activation** — the advisory tiers become actual stake recommendations driving the spend ladder.
- **Stack detection revival** — when 2+ batters on the same team face the same vulnerable pitcher, boost their primary_signal (was in v2, removed in Phase 1, will return Phase 2).
- **2+ bases market** — unified anchor expands from 2 markets (HR, HRR) to 3 (HR, HRR, 2+bases).
- **Heat math fix** — the combined_trend computation in compute_batter_trends.py has a geometric-mean dilution bug. Heat isn't used by the picker anymore (Phase 1 removed it), but the frontend may still display it. Fix or remove.
- **Handedness-aware lookups** — currently the picker uses `vs_hand='A'` (combined) for all stats. Future enhancement: read `vs_hand='L'` when facing an LHP, `vs_hand='R'` when facing an RHP.

---

## Audit Logs to Watch

Every picker run emits these audit lines (visible in GitHub Actions logs):

**BvP coverage pre-flight:**

```
BvP pairs: N (M distinct batters with bvp, X.X% slate coverage)
```

If coverage drops below 20%, Gate B will rarely fire and a warning is logged.

**Signal distribution:**

```
Signal distribution: n=N min=X.XXX med=X.XXX max=X.XXX
  by stake tier: lock=N safe=N risky=N lottery=N donation=N
  by source:     bvp_observed=N pitch_type_observed=N l7_power_form=N l14_power_form=N none=N
```

Tells you what conviction looks like today and which signal is driving picks. If every pick is `l7_power_form` and no `bvp_observed` / `pitch_type_observed` fires, that's a sign matchup data isn't engaging — worth investigating.

**Slate coverage (after per-game straights ship):**

```
Slate coverage: N/M games represented across all cards + straights
Straight picks: N (Lock=X, Safe=Y, demoted via EV screen=Z)
```

---

## Summary

The model picks batters who have a real read on doing the thing (matchup + skill + recent form), in that priority order. It demotes stake confidence when the market disagrees, but doesn't drop picks unless the disagreement is severe. It's honest about how strong each read is — Lock means Lock, Lottery means Lottery. The tier system mirrors a real bankroll-builder strategy (2U / 1U / 0.25U / 0.10U / 0.05U), but activation waits on calibration data to prove the buckets are honest.

The architecture replaces a v2 system that overindexed on EV (the price) and missed obvious matchup gold (Refsnyder, Yordan). Phase 1 corrects this. Whether the new picks actually win at the predicted rates is the open question that calibration will answer over the coming weeks.
