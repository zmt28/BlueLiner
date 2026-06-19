"""Unit tests for the deterministic guardrails (no LLM/key required)."""

from __future__ import annotations

from agent import guardrails


def _proposal(*river_ids, why=None):
    return {
        "recommendations": [
            {"river_id": r, "name": r, "verdict": "go", "overall_score": "green",
             "confidence": "high", "why": why or [], "sources": []}
            for r in river_ids
        ],
        "blocked": [], "notes": "",
    }


def test_flood_is_blocked():
    ev = {"a": {"flow_ratio": 4.0, "water_temp_f": 55, "public_access": True}}
    out = guardrails.apply(_proposal("a"), ev)
    assert out["recommendations"] == []
    assert any(b["river_id"] == "a" for b in out["blocked"])
    assert any(v["rule"] == "flood" for v in out["violations"])


def test_too_warm_is_blocked():
    ev = {"a": {"flow_ratio": 1.0, "water_temp_f": 72, "public_access": True}}
    out = guardrails.apply(_proposal("a"), ev)
    assert out["recommendations"] == []
    assert any(v["rule"] == "too_warm" for v in out["violations"])


def test_private_access_is_blocked():
    ev = {"a": {"flow_ratio": 1.0, "water_temp_f": 55, "public_access": False}}
    out = guardrails.apply(_proposal("a"), ev)
    assert out["recommendations"] == []
    assert any(v["rule"] == "access" for v in out["violations"])


def test_too_cold_is_demoted_not_blocked():
    ev = {
        "warm_ok": {"flow_ratio": 1.0, "water_temp_f": 55, "public_access": True},
        "cold": {"flow_ratio": 1.0, "water_temp_f": 38, "public_access": True},
    }
    out = guardrails.apply(_proposal("cold", "warm_ok"), ev)
    ids = [r["river_id"] for r in out["recommendations"]]
    assert set(ids) == {"cold", "warm_ok"}        # both survive
    assert ids[-1] == "cold"                        # cold demoted to last
    assert any(v["rule"] == "too_cold" for v in out["violations"])


def test_staleness_lowers_confidence():
    ev = {"a": {"flow_ratio": 1.0, "water_temp_f": 55, "public_access": True,
                "last_updated_hours_ago": 12}}
    out = guardrails.apply(_proposal("a"), ev)
    assert out["recommendations"][0]["confidence"] == "low"
    assert any(v["rule"] == "staleness" for v in out["violations"])


def test_grounding_flags_invented_number():
    ev = {"a": {"flow_ratio": 1.0, "flow_cfs": 90, "water_temp_f": 55,
                "public_access": True}}
    # 999 cfs appears nowhere in the evidence.
    out = guardrails.apply(_proposal("a", why=["flow is 999 cfs, perfect"]), ev)
    assert out["grounding_ok"] is False
    assert 999.0 in out["unsourced"]


def test_id_reformatting_cannot_bypass_guardrail():
    # The model sometimes reformats ids (underscores for hyphens). The evidence
    # lookup must canonicalize so a flood can't slip through unmatched.
    ev = {"penns-creek-pa": {"flow_ratio": 4.0, "water_temp_f": 58, "public_access": True}}
    out = guardrails.apply(_proposal("penns_creek_pa"), ev)
    assert out["recommendations"] == []
    assert any(v["rule"] == "flood" for v in out["violations"])
    # canonical id written back
    assert out["blocked"][0]["river_id"] == "penns-creek-pa"


def test_grounding_accepts_sourced_number():
    ev = {"a": {"flow_ratio": 1.0, "flow_cfs": 90, "water_temp_f": 55,
                "public_access": True}}
    out = guardrails.apply(_proposal("a", why=["flow 90 cfs, water 55F"]), ev)
    assert out["grounding_ok"] is True
