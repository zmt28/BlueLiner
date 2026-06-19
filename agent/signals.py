"""Feature extraction for a reach — assembles the inputs coldwater_suitability
scores. Kept separate from the scorer so each signal is a named, inspectable
feature (and so the backtest can ablate them).

Signals (spec section 5):
  topology  — proximity to designated trout water (reach_data, geometry proxy)
  flow/size — stream order + length (holds fish year-round)
  thermal   — nearest same-network gauge reading if available, else inferred
  access    — public/permit access nearby (binding actionability filter)
"""

from __future__ import annotations

from typing import Optional

from . import reach_data


def extract(reach: dict, topo_index: dict, thermal: Optional[dict] = None) -> dict:
    """`reach` is a reach_data record; `topo_index` from make_topology_index."""
    topo = reach_data.topology_at(topo_index, reach["lat"], reach["lon"],
                                  reach["gnis_name"])
    access = reach_data.access_for(reach["comid"])
    flow = {"streamorder": reach["streamorder"], "lengthkm": reach["lengthkm"]}
    thermal = thermal or {"water_temp_f": None, "gauged": False}
    return {
        "comid": reach["comid"], "name": reach["gnis_name"],
        "levelpathid": reach["levelpathid"], "lat": reach["lat"], "lon": reach["lon"],
        "topology": topo, "flow": flow, "thermal": thermal, "access": access,
    }
