# BlueLines -- Product Spec

## Problem Statement

Fly fishermen make go/no-go decisions about fishing trips based on stream conditions.
Current tools -- the USGS water data website, scattered fishing forums, word of mouth --
are fragmented, slow to navigate, and not built for quick mobile use on the water.
A single glance at current conditions across nearby streams should be all it takes to
decide where to go. BlueLines consolidates live USGS sensor data into a fast, map-based
view that answers one question: which streams are fishable right now?

## Target User

Freshwater fly fishermen, primarily trout anglers, who fish moving water (streams and
rivers) and need to check conditions before or during a trip. These users are already
checking USGS data manually -- they just need a faster, more visual way to do it.

## Core Use Case

A user opens BlueLines the morning before a planned fishing trip, sees the map with
color-coded markers, and immediately knows which nearby streams are in fishable
condition. No clicking through multiple USGS pages, no mental math on whether the
flow is too high. Green means go.

## V1 Scope (shipped)

- Live USGS instantaneous values data for Maryland, Virginia, and West Virginia streams
- Interactive map with sensor readings per monitoring station
- Clickable popups showing flow, temperature, and all available readings
- Census TIGER/Line waterway geometries rendered on the map
- Fishing conditions scoring (green/yellow/red) based on temperature and flow

## V2 Roadmap

- Mobile-responsive layout for checking conditions at the truck or on the water
- Species filter -- show only stations relevant to trout, bass, or other target species
- Insect hatch guidance and trout-stocking overlay per region
- Historical conditions charting per station, served live from the USGS Daily
  Values API (~1 year of daily data; not stored locally)
- Saved map pins and fishing-session logs -- a "Strava for fishing" record of
  where and when you fished, built on the local datastore
- User-configurable alert thresholds for favorite streams
- Push notifications when a saved stream hits ideal conditions

## Success Metrics

- **Time to conditions check:** under 10 seconds from app open to readable map
- **Data freshness:** sensor readings no more than 15 minutes stale (limited by USGS
  update frequency)
- **Coverage:** all active USGS stream monitoring stations in supported states
