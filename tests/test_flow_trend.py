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
