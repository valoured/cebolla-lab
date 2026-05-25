"""
calibration.py — Patch 8: daily model-calibration check + auto-rollback.

Runs daily (4:00 AM ET via GitHub Actions — cron wiring is a follow-up commit).
Pulls the trailing `calibration_lookback_days` of SETTLED pods + cards and
computes two hit rates:

  - A-tier hit rate  (confidence_tier in A+/A/A-, pods + cards): if below
    a_tier_hit_floor → log a HIGH-severity alert. No automatic action.
  - POD HR hit rate  (pods, market_class='hr'): if below pod_hr_hit_floor →
    log a CRITICAL alert AND auto-roll-back the framework to v1.

SAFETY GUARD: each cohort needs >= calibration_min_sample settled picks or the
check is SKIPPED (logs "insufficient sample") — variance on a thin slate must
never trip the kill switch.

"Hit" = status == 'win'; "settled" = status in ('win','loss') (void/push/pending
excluded from BOTH numerator and denominator). There is no result_hit column;
status is the source of truth (see settle_pods.py / settle_cards.py).

Thresholds load from model_thresholds (tunable). Writes: model_calibration_alerts
(alerts) and, on rollback, version_history + model_thresholds (auto_rollback_to_v1).
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

from supabase import create_client
from dotenv import load_dotenv

from tier_system import load_thresholds, configure, _cfg_num

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("calibration")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# Framework version tags — must match the version_history seeds (migration 23/25).
V2_TAG = "v2-launch-2026-05-26"
V1_TAG = "v1-final-2026-05-25"

# Confidence letters that count as "A-tier".
A_TIER = {"A+", "A", "A-"}

# Settled outcomes used for hit-rate math (void/push/pending excluded entirely).
SETTLED = ["win", "loss"]


# ─── Date helpers ──────────────────────────────────────────────────────────────

def get_today_iso():
    """ET-relative date (same convention as the pickers)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def since_date(lookback_days):
    """ISO date `lookback_days` before today (ET)."""
    et_today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    return (et_today - timedelta(days=int(lookback_days))).isoformat()


# ─── Fetch + hit-rate helpers ──────────────────────────────────────────────────

def fetch_settled_pods(since):
    """Settled pods (status in win/loss) on/after `since`. Empty list on error."""
    try:
        res = sb.table("pods") \
            .select("pod_date, status, confidence_tier, market_class") \
            .gte("pod_date", since) \
            .in_("status", SETTLED) \
            .execute()
        return res.data or []
    except Exception as e:
        log.warning("fetch_settled_pods failed: %s", e)
        return []


def fetch_settled_cards(since):
    """Settled cards (status in win/loss) on/after `since`. Empty list on error."""
    try:
        res = sb.table("cards") \
            .select("card_date, status, confidence_tier") \
            .gte("card_date", since) \
            .in_("status", SETTLED) \
            .execute()
        return res.data or []
    except Exception as e:
        log.warning("fetch_settled_cards failed: %s", e)
        return []


def hit_rate(rows):
    """
    (rate, n) over settled rows: rate = wins / n, a win = status == 'win', n =
    settled denominator (rows are already win/loss only). (None, 0) if empty.
    """
    n = len(rows)
    if n == 0:
        return (None, 0)
    wins = sum(1 for r in rows if r.get("status") == "win")
    return (wins / n, n)


# ─── Checks ─────────────────────────────────────────────────────────────────────

def log_alert(severity, issue, rate, action_recommended=None, action_taken=None):
    """Insert one row into model_calibration_alerts."""
    try:
        sb.table("model_calibration_alerts").insert({
            "severity": severity,
            "issue": issue,
            "rate": round(rate, 5) if rate is not None else None,
            "action_recommended": action_recommended,
            "action_taken": action_taken,
            "resolved": False,
        }).execute()
        log.info("alert logged: severity=%s issue=%r rate=%s action_taken=%s",
                 severity, issue,
                 f"{rate:.3f}" if rate is not None else None, action_taken)
    except Exception as e:
        log.error("failed to insert calibration alert (%s / %s): %s", severity, issue, e)


def check_a_tier(pods, cards, cfg):
    """
    A-tier hit rate across pods + cards (confidence_tier in A+/A/A-). If below
    a_tier_hit_floor AND the cohort has >= calibration_min_sample settled picks,
    log a HIGH-severity alert. NO auto-action (A-tier collapse is a warning, not
    a kill trigger). Returns the rate, or None if skipped/insufficient.
    """
    floor = _cfg_num(cfg, "a_tier_hit_floor", 0.15)
    min_sample = int(_cfg_num(cfg, "calibration_min_sample", 20))
    a_rows = [r for r in (pods + cards) if r.get("confidence_tier") in A_TIER]
    rate, n = hit_rate(a_rows)
    if n < min_sample:
        log.info("A-tier check SKIPPED — insufficient sample (%d < %d settled A-tier picks)",
                 n, min_sample)
        return None
    log.info("A-tier hit rate: %.3f over %d settled picks (floor %.3f)", rate, n, floor)
    if rate < floor:
        log_alert("high", "A-tier hit rate collapsed", rate,
                  action_recommended="Review A-tier confidence calibration; no auto-action taken.")
    return rate


def check_pod_hr(pods, cfg):
    """
    POD HR hit rate (pods, market_class='hr'). If below pod_hr_hit_floor AND the
    cohort has >= calibration_min_sample settled picks, log a CRITICAL alert and
    AUTO-ROLL-BACK to v1. Returns the rate, or None if skipped/insufficient.
    """
    floor = _cfg_num(cfg, "pod_hr_hit_floor", 0.12)
    min_sample = int(_cfg_num(cfg, "calibration_min_sample", 20))
    hr_rows = [r for r in pods if r.get("market_class") == "hr"]
    rate, n = hit_rate(hr_rows)
    if n < min_sample:
        log.info("POD HR check SKIPPED — insufficient sample (%d < %d settled POD HR picks)",
                 n, min_sample)
        return None
    log.info("POD HR hit rate: %.3f over %d settled picks (floor %.3f)", rate, n, floor)
    if rate < floor:
        log_alert("critical", "POD HR hit rate below floor", rate,
                  action_recommended="Auto-rollback to v1 triggered.",
                  action_taken="auto_rollback_to_v1")
        auto_rollback_to_v1(cfg)
    return rate


# ─── Auto-rollback (kill switch) ─────────────────────────────────────────────────

def auto_rollback_to_v1(cfg=None):
    """
    Patch 8 kill switch — revert the framework to v1.

    IDEMPOTENT: if v1 is already active (a prior rollback fired), log and return —
    no re-flip, no daily noise.

    ORDERING (deliberate): flip version_history flags FIRST (v2→inactive,
    v1→active), THEN restore model_thresholds from v1's config snapshot. On a
    partial failure this leaves state in the SAFER direction — the picker reads v1
    as active even if some thresholds didn't restore; the reverse would run v1
    thresholds while v2 is still marked active (inconsistent).

    Failure handling:
      - flag flip fails → return early, touch nothing else, log loudly; state
        stays consistent at v2.
      - threshold restore fails (per key) → log loudly + write an extra CRITICAL
        model_calibration_alerts row listing the failed keys for manual repair
        from version_history.config.

    Restores only the v1 numeric keys from the snapshot; v2-NEW keys are left
    alone (harmless when the v1 code paths that read them aren't running), and
    non-numeric entries (the "_source" marker, the heat-tier set) are skipped.

    # TODO: non-atomic multi-table write. supabase-py REST has no transactions.
    # True atomicity needs a Postgres RPC. Ordering (flag first, thresholds
    # second) ensures partial failure leaves state in the safer direction.
    # Separate follow-up commit for the RPC.

    Returns True if a rollback was performed, False otherwise.
    """
    # ── Idempotency: is v1 already active? ──
    try:
        vh = sb.table("version_history").select("version_tag, active, config").execute()
    except Exception as e:
        log.error("rollback ABORTED — could not read version_history: %s", e)
        return False
    rows = {r["version_tag"]: r for r in (vh.data or [])}
    v1 = rows.get(V1_TAG)
    if v1 and v1.get("active"):
        log.info("v1 already active, skipping rollback")
        return False
    if not v1:
        log.error("rollback ABORTED — v1 version_history row (%s) not found", V1_TAG)
        return False

    # ── Step 1: flip flags FIRST (safer direction on partial failure) ──
    try:
        sb.table("version_history").update({"active": False}) \
            .eq("version_tag", V2_TAG).execute()
        sb.table("version_history").update(
            {"active": True, "rollback_reason": "auto: POD HR hit rate below floor"}
        ).eq("version_tag", V1_TAG).execute()
        log.warning("ROLLBACK: version_history flipped — %s inactive, %s ACTIVE", V2_TAG, V1_TAG)
    except Exception as e:
        log.error("rollback ABORTED — flag flip failed: %s (state stays consistent at v2)", e)
        return False

    # ── Step 2: restore v1 thresholds into model_thresholds (v1 numeric keys only) ──
    v1_config = v1.get("config") or {}
    failed_keys = []
    restored = 0
    for key, value in v1_config.items():
        if key.startswith("_"):
            continue  # e.g. "_source" provenance marker
        if isinstance(value, (list, dict)):
            continue  # e.g. heat-tier set — stored as text_value, not num_value
        try:
            sb.table("model_thresholds").update({"num_value": value}) \
                .eq("key", key).execute()
            restored += 1
        except Exception as e:
            log.error("rollback: failed to restore threshold %r=%s: %s", key, value, e)
            failed_keys.append(key)

    log.warning("ROLLBACK: restored %d v1 threshold(s); %d failed", restored, len(failed_keys))
    if failed_keys:
        log_alert("critical",
                  "rollback threshold restore failed for keys: " + ", ".join(failed_keys),
                  None,
                  action_recommended="Manually restore listed keys from version_history.config "
                                     f"(version_tag={V1_TAG}).")
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = get_today_iso()
    log.info("🧅 Calibration check — %s", today)

    try:
        cfg = load_thresholds(sb)
        configure(cfg)
        log.info("Loaded %d thresholds from model_thresholds.", len(cfg))
    except Exception as e:
        cfg = {}
        log.warning("model_thresholds load failed (%s) — using _DEFAULTS fallbacks.", e)

    lookback = int(_cfg_num(cfg, "calibration_lookback_days", 14))
    since = since_date(lookback)
    log.info("Window: settled picks since %s (%d-day lookback)", since, lookback)

    pods = fetch_settled_pods(since)
    cards = fetch_settled_cards(since)
    log.info("Settled in window: %d pods, %d cards", len(pods), len(cards))

    check_a_tier(pods, cards, cfg)
    check_pod_hr(pods, cfg)   # may trigger auto_rollback_to_v1

    log.info("🧅 Calibration check complete")


if __name__ == "__main__":
    main()
