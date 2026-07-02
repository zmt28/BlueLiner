"""M4.4: flow-trend classification (diff vs the prior snapshot) and its
popup rendering."""

import main


def test_flow_trend_thresholds_at_one_hour():
    f = main._flow_trend
    assert f(125, 100, 1.0) == "rising_fast"     # +25%/h
    assert f(110, 100, 1.0) == "rising"          # +10%/h
    assert f(102, 100, 1.0) == "steady"          # +2%/h
    assert f(90, 100, 1.0) == "falling"          # -10%/h
    assert f(70, 100, 1.0) == "falling_fast"     # -30%/h


def test_flow_trend_normalizes_by_interval():
    # +10% over 2 hours = +5%/h -> the rising boundary.
    assert main._flow_trend(110, 100, 2.0) == "rising"
    # The same +10% over 30 minutes = +20%/h -> rising fast.
    assert main._flow_trend(110, 100, 0.5) == "rising_fast"


def test_flow_trend_guards():
    f = main._flow_trend
    assert f(None, 100, 1.0) is None
    assert f(100, None, 1.0) is None
    assert f(100, 0, 1.0) is None            # zero prior -> undefined rate
    assert f(100, 100, None) is None
    assert f(100, 100, 0.01) is None         # same reading re-observed
    assert f(200, 100, 12.0) is None         # too stale to call a trend


def test_flow_context_html_renders_trend_chip():
    base = {"current_flow": 142.0}
    html = main._flow_context_html(base, 120.0)
    assert "142 cfs now" in html
    assert "rising" not in html               # no trend -> no chip

    html = main._flow_context_html({**base, "trend": "rising_fast"}, 120.0)
    assert "rising fast" in html and "&#8599;&#8599;" in html

    html = main._flow_context_html({**base, "trend": "falling"}, 120.0)
    assert "falling" in html and "&#8600;" in html


# -- Fishable window (M4.4b) --------------------------------------------

def _cond(flow_score, cur, trend):
    return {"flow": flow_score, "current_flow": cur, "trend": trend}


def test_window_open_and_closing_on_good_flow():
    w = main._flow_window(_cond("green", 120, "steady"), 100.0, 0.0)
    assert w == ("open", "Good window now")
    w = main._flow_window(_cond("green", 120, "falling"), 100.0, -0.1)
    assert w[0] == "open"
    w = main._flow_window(_cond("green", 150, "rising_fast"), 100.0, 0.3)
    assert w[0] == "closing" and "closing" in w[1]


def test_window_projects_reentry_when_blown_out_and_dropping():
    # 4x median falling 10%/h: h = ln(0.5)/ln(0.9) ~ 6.6h -> later today.
    w = main._flow_window(_cond("red", 400, "falling"), 100.0, -0.10)
    assert w == ("dropping_in", "Blown out but dropping — window later today")
    # Same spike decaying 3%/h: ~22.8h -> tomorrow.
    w = main._flow_window(_cond("red", 400, "falling"), 100.0, -0.03)
    assert w[1].endswith("tomorrow")
    # 1%/h: ~69h -> a couple of days.
    w = main._flow_window(_cond("red", 400, "falling"), 100.0, -0.01)
    assert w[1].endswith("in a couple of days")
    # 0.3%/h: > 72h out -> too far to promise anything.
    assert main._flow_window(_cond("red", 400, "falling"), 100.0, -0.003) is None


def test_window_blown_out_and_not_dropping():
    w = main._flow_window(_cond("red", 400, "rising"), 100.0, 0.10)
    assert w == ("none_yet", "Blown out — no window yet")
    w = main._flow_window(_cond("red", 350, "steady"), 100.0, 0.0)
    assert w[0] == "none_yet"


def test_window_stays_silent_when_ambiguous():
    # Yellow-steady: nothing honest to say.
    assert main._flow_window(_cond("yellow", 220, "steady"), 100.0, 0.0) is None
    # No median -> no band to project against (unless flow is green).
    assert main._flow_window(_cond("red", 400, "falling"), None, -0.1) is None
    # No trend yet (first observation).
    assert main._flow_window(_cond("red", 400, None), 100.0, None) is None


def test_window_renders_in_flow_context_html():
    html = main._flow_context_html(
        {"current_flow": 400.0, "trend": "falling",
         "window": "dropping_in",
         "window_label": "Blown out but dropping — window tomorrow"},
        100.0)
    assert "window tomorrow" in html and "#2980b9" in html
    html = main._flow_context_html({"current_flow": 120.0}, 100.0)
    assert "window" not in html.lower()
