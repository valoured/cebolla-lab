"""
weather_index.py — v2 weather→HR multiplier (Day 3). Pure, DB-free.

compute_weather_hr_index(temp_f, humidity_pct, wind_mph, wind_dir_deg,
                         park_cf_bearing, roof_status) -> float

Returns a weather-only HR multiplier (1.000 = neutral), clamped to [0.6, 1.5].
Park, batter, and pitcher effects are handled separately by the v2 model.

Components (additive around 1.0):
  wind  — direction is rotated FROM→TOWARD before comparing to the
          home-plate→CF bearing (Open-Meteo reports the direction wind blows
          FROM). Blowing OUT toward CF boosts; blowing IN suppresses; crosswind
          ~neutral. Scaled (mph/10)*0.05; no effect below 5 mph.
  temp  — (temp_f - 70) / 100  (hot air carries)
  humid — (humidity_pct - 50) / 500  (humid air is LESS dense → more carry)

Roof:
  'closed'                → 1.000 immediately (weather sealed out)
  'retractable_uncertain' → 0.5× each component (unknown open/closed state)
  anything else / 'no_roof' → full effect
"""

NEUTRAL = 1.000


def _angular_diff(a: float, b: float) -> float:
    """Smallest absolute angle (0-180) between two compass bearings."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def compute_weather_hr_index(temp_f, humidity_pct, wind_mph, wind_dir_deg,
                             park_cf_bearing, roof_status) -> float:
    # Closed roof: weather is sealed out — neutral regardless of conditions.
    if roof_status == "closed":
        return NEUTRAL

    # Extreme-heat retractable heuristic: at >=95°F a retractable roof is almost
    # certainly CLOSED (AC), so the open-air weather signal is misleading. Treat
    # it as closed → neutral. Handles e.g. Chase Field at 108°F (ARI), and the
    # same for HOU/TEX/MIA/MIL/TOR/SEA in extreme heat.
    if roof_status == "retractable_uncertain" and temp_f is not None and float(temp_f) >= 95.0:
        return NEUTRAL

    # ── Wind ──────────────────────────────────────────────────────────────
    # Open-Meteo wind_dir_deg = direction the wind blows FROM. Convert to the
    # direction it blows TOWARD (+180) before measuring offset from the
    # home-plate→CF bearing, so "wind out to CF" = small diff = boost.
    if wind_dir_deg is None or park_cf_bearing is None:
        diff = 90.0   # unknown geometry → treat as neutral crosswind
    else:
        blowing_toward = (float(wind_dir_deg) + 180.0) % 360.0
        diff = _angular_diff(blowing_toward, float(park_cf_bearing))

    wind_effect = 0.0
    if wind_mph is not None and float(wind_mph) >= 5.0:
        w = float(wind_mph)
        if diff < 45.0:            # blowing OUT toward CF → boost
            wind_effect = (w / 10.0) * 0.05
        elif diff > 135.0:         # blowing IN from CF → suppress
            wind_effect = -(w / 10.0) * 0.05
        # 45°-135° → crosswind → ~0

    # ── Temp / humidity ───────────────────────────────────────────────────
    temp_effect = ((float(temp_f) - 70.0) / 100.0) if temp_f is not None else 0.0
    humidity_effect = ((float(humidity_pct) - 50.0) / 500.0) if humidity_pct is not None else 0.0

    # Retractable, open/closed unknown → discount the whole weather signal.
    if roof_status == "retractable_uncertain":
        wind_effect *= 0.5
        temp_effect *= 0.5
        humidity_effect *= 0.5

    total = 1.0 + wind_effect + temp_effect + humidity_effect
    return round(max(0.6, min(1.5, total)), 3)


# ── Unit self-tests (pure; no DB, no network) ────────────────────────────
def _run_selftests():
    WR = 30  # Wrigley home-plate→CF bearing (teams.home_plate_bearing for CHC)

    # 15 mph blowing straight OUT at Wrigley → wind FROM 210 (toward CF 30).
    # NOTE: the spec annotated this as "270° FROM", but 270 FROM at CF=30 is a
    # 60° crosswind (~neutral); straight-OUT requires FROM=210. We use the
    # geometrically-correct input; the TARGET index 1.075 is preserved.
    assert compute_weather_hr_index(70, 50, 15, 210, WR, "no_roof") == 1.075
    # 15 mph blowing straight IN → wind FROM 30 (toward home) → 0.925
    assert compute_weather_hr_index(70, 50, 15, 30, WR, "no_roof") == 0.925
    # 15 mph crosswind → wind FROM 120 (toward 300, 90° off CF) → ~1.000
    assert compute_weather_hr_index(70, 50, 15, 120, WR, "no_roof") == 1.000
    # For reference, the spec's literal 270°-FROM input is a crosswind (neutral):
    assert compute_weather_hr_index(70, 50, 15, 270, WR, "no_roof") == 1.000
    # Closed roof (TB) → 1.000 regardless of weather
    assert compute_weather_hr_index(95, 90, 25, 210, 45, "closed") == 1.000
    # Retractable uncertain → 0.5× discount: out wind +0.075 → +0.0375 → 1.038
    assert compute_weather_hr_index(70, 50, 15, 210, WR, "retractable_uncertain") == 1.038
    # Hot day (95°F) + tailwind out → boost (0.25 temp + 0.075 wind = 1.325)
    assert compute_weather_hr_index(95, 50, 15, 210, WR, "no_roof") == 1.325
    # Cold day (45°F) + headwind in → suppress (-0.25 temp - 0.075 wind = 0.675)
    assert compute_weather_hr_index(45, 50, 15, 30, WR, "no_roof") == 0.675
    # Sub-5 mph wind → no wind effect (only temp/humidity)
    assert compute_weather_hr_index(70, 50, 3, 210, WR, "no_roof") == 1.000
    # Humid air carries: 90% humidity → +0.08 → 1.080 (calm, baseline temp)
    assert compute_weather_hr_index(70, 90, 0, 210, WR, "no_roof") == 1.080

    # Extreme-heat retractable heuristic: 108°F + 13 mph out → roof assumed
    # closed → 1.000 (NOT the +15.5% the open-air calc would give).
    assert compute_weather_hr_index(108, 50, 13, 210, WR, "retractable_uncertain") == 1.000
    # Same park/wind at a moderate 75°F → heuristic does NOT fire; normal
    # retractable 0.5× discount applies (wind +0.0325, temp +0.025 → ~1.058).
    warm = compute_weather_hr_index(75, 50, 13, 210, WR, "retractable_uncertain")
    assert warm != 1.000 and 1.055 <= warm <= 1.060
    # …and the discount really bit: open-air version of the same is higher.
    assert warm < compute_weather_hr_index(75, 50, 13, 210, WR, "no_roof")
    print("weather_index self-tests: ALL PASSED")


if __name__ == "__main__":
    _run_selftests()
