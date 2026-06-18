"""Pin agent/scorer.py to Blueliner's production scorer.

The agent's scorer (clean Fahrenheit/CFS signature) must produce the SAME
verdict as `main.score_conditions` (raw USGS-variable signature) for every
input. This test exercises both across a dense grid so the agent's grounding
tool and the eval oracle can never silently drift from what the app shows users.

`main` pulls in FastAPI; if the app deps aren't installed (e.g. a minimal
environment), the test skips rather than failing -- CI installs them, so the
parity check runs there.
"""

from __future__ import annotations

import pytest

from agent.scorer import score_conditions as agent_score

main = pytest.importorskip(
    "main", reason="Blueliner app (FastAPI) not installed; parity runs in CI"
)


def _prod(water_temp_f, flow_cfs, median_cfs):
    """Call the production scorer with its native variable-list signature."""
    variables = []
    if water_temp_f is not None:
        # main converts C->F internally, so feed Celsius.
        variables.append(
            {"variable": "Temperature, water, degrees Celsius",
             "value": str((water_temp_f - 32) * 5 / 9)}
        )
    if flow_cfs is not None:
        variables.append(
            {"variable": "Discharge, cubic feet per second",
             "value": str(flow_cfs)}
        )
    return main.score_conditions(variables, median_cfs)


# Avoid exact float-fragile band edges (45/48/65/68/40 F) where the C<->F
# round-trip inside main can land a hair on either side; step 0.5 off them.
TEMPS = [None, 36.0, 39.5, 41.0, 44.0, 46.5, 49.0, 55.0, 60.0, 64.5,
         66.5, 67.5, 69.0, 72.0, 80.0]
FLOWS = [None, -5.0, 0.0, 12.0, 24.0, 30.0, 99.0, 100.0, 250.0, 305.0,
         900.0, 5001.0, 9000.0, 10500.0]
MEDIANS = [None, 50.0, 100.0, 400.0]


@pytest.mark.parametrize("temp_f", TEMPS)
@pytest.mark.parametrize("flow_cfs", FLOWS)
@pytest.mark.parametrize("median_cfs", MEDIANS)
def test_parity(temp_f, flow_cfs, median_cfs):
    if temp_f is None and flow_cfs is None:
        return  # nothing to score; both return gray, not interesting
    prod = _prod(temp_f, flow_cfs, median_cfs)
    mine = agent_score(temp_f, flow_cfs, median_cfs)
    assert mine["overall"] == prod["overall"], (temp_f, flow_cfs, median_cfs, mine, prod)
    assert mine["temp_state"] == prod["temp"], (temp_f, mine, prod)
    assert mine["flow_state"] == prod["flow"], (flow_cfs, median_cfs, mine, prod)
