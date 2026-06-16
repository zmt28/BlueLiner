# Endpoint watch

_Run: 2026-06-16 11:03 UTC -- 9/11 reachable, **7 READY TO PROMOTE**._

| id | state | kind | status | captured |
|----|-------|------|--------|----------|
| md-designated-use-field | MD | field_dump | DOWN | - |
| md-fisheries-folder | MD | discover | UP | yes |
| nv-lct-successor | NV | discover | UP | yes |
| cand-stocking-ma-massgis-dfg-trout-stocking--official-item | MA | verify | DOWN | - |
| cand-access_points-de-dnrec-public-fishing-ponds | DE | verify | UP | PROMOTE |
| cand-access_points-ca-cdfw-fishing-guide-boating-facilities | CA | verify | UP | PROMOTE |
| cand-access_points-ut-ut-dwr-community-fisheries | UT | verify | UP | PROMOTE |
| cand-access_points-tx-tx-public-water-access---boat-ramps--owner-uncon | TX | verify | UP | PROMOTE |
| cand-access_points-sd-sd-boat-ramps--not-authoritative----personal-ago | SD | verify | UP | PROMOTE |
| cand-access_points-nc-ncwrc-boating-access-areas--inland | NC | verify | UP | PROMOTE |
| cand-access_points-al-al-boat-ramps--third-party-national-org | AL | verify | UP | PROMOTE |

## Captured detail (reachable entries)

### md-fisheries-folder (MD / discover)
> Enumerate MD DNR Fisheries services for a wild-trout / special-management layer richer than DesignatedUse_Trout.

- **AGOL search matches:**
  - _(reachable, no fish/trout items)_

### nv-lct-successor (NV / discover)
> NDOW retired LCT_Occupied_Streams_NV (all layerIds 404). Find the republished Lahontan cutthroat occupied-streams service.

- **services / layers matching fish/trout:**
  - `LCT_Occupied_Lakes` (FeatureServer)
  - `LCT_Occupied_Streams_NV` (FeatureServer)
  - `NDOW_Fishable_Streams_WFL1` (FeatureServer)
  - `NDOWFishableWaters` (FeatureServer)
  - `Nevada_Department_of_Wildlife_Fish_Hatchery_Locations` (FeatureServer)
  - `Red_Banded_Trout_Distributions` (FeatureServer)
  - `Mule_Deer_Trout_Creek_Herd_All` (FeatureServer)

### cand-access_points-de-dnrec-public-fishing-ponds (DE / verify)
> [access_points candidate] DNREC Public Fishing Ponds

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`Depths`, geom `esriGeometryPoint`, count `51`)
- **fields:** `["OBJECTID", "DEPTH", "POND", "SHAPE"]`

### cand-access_points-ca-cdfw-fishing-guide-boating-facilities (CA / verify)
> [access_points candidate] CDFW Fishing Guide Boating Facilities

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`FGuideBoating`, geom `esriGeometryPoint`, count `677`)
- **fields:** `["OBJECTID", "Person_Interviewed", "Interview_Date", "Comments", "Facility_Name", "Facility_Address", "City", "Zipcode", "Mailing_Address", "Mailing_City", "Mailing_Zipcode", "Phone_Num", "Fax_Number", "Website", "EMail_Address", "Water_Body", "County", "Geographic_Area_or_Region", "Owner", "Jurisdiction_or_Authority", "Type_of_Ownership_Gov_or_Non", "Facility_Users", "Sewage_or_Bilge_Pumpouts", "Used_Oil_Collection", "Dry_Storage_Capacity", "Total_Capacity_Slips_or_Tie_Ups", "Longitude", "Latitude", "gis_GIS_COREDATA_1_FacilityID", "Leased"]`

### cand-access_points-ut-ut-dwr-community-fisheries (UT / verify)
> [access_points candidate] UT DWR Community Fisheries

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`CommunityFisheries_May2021`, geom `esriGeometryPoint`, count `64`)
- **fields:** `["OBJECTID_1", "OBJECTID", "WaterName", "Acres", "Location", "Amenities", "Species", "HandicapAccess", "ContactInf0", "County", "LONG_PARK", "LAT_PARK"]`

### cand-access_points-tx-tx-public-water-access---boat-ramps--owner-uncon (TX / verify)
> [access_points candidate] TX Public Water Access / Boat Ramps (owner unconfirmed)

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`BoatRamps_Public`, geom `esriGeometryPoint`, count `2235`)
- **fields:** `["OBJECTID", "PWAID", "TPWAID", "AccessType", "AccessTypeDescription", "Longitude", "Latitude", "GlobalID"]`

### cand-access_points-sd-sd-boat-ramps--not-authoritative----personal-ago (SD / verify)
> [access_points candidate] SD boat ramps (NOT authoritative -- personal AGOL)

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`Boat Ramps (zoomed out)`, geom `esriGeometryPoint`, count `376`)
- **fields:** `["OBJECTID", "Name", "RampLevel", "SurfaceType", "NumLanes", "Status", "Comments", "Fee", "Ownership", "CarteID", "Display", "GlobalID"]`

### cand-access_points-nc-ncwrc-boating-access-areas--inland (NC / verify)
> [access_points candidate] NCWRC Boating Access Areas (inland)

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`NCWRC_Boating_Access_Areas_InlandFishing`, geom `esriGeometryPoint`, count `184`)
- **fields:** `["OBJECTID", "Site_Status", "BAA_Name", "BAA_Alias", "Water_Access", "Region", "Work_Area", "Maintenance", "Street", "City", "Zip", "County", "Phone", "Website", "Longitude", "Latitude", "Owner", "Yr_Added_To_Program", "Fishing_Water_Designation", "Launch_Lane_No", "Fix_Dock_No", "Float_Dock_No", "Courtesy_Dock_No", "Fishing_Pier", "Parking_Trailer", "Parking_Trailer_ADA", "Parking_nonTrailer", "Parking_nonTrailer_ADA", "Parking_Surface", "ADA_Paved_Walkway"]`

### cand-access_points-al-al-boat-ramps--third-party-national-org (AL / verify)
> [access_points candidate] AL boat ramps (third-party national org)

- :rotating_light: **READY TO PROMOTE** -- candidate passes the 4-check. Review + add to sources.json (human-gated; the watcher does not auto-edit).
- **verdict:** PASS  (`Alabama Boat Ramps`, geom `esriGeometryPoint`, count `33`)
- **fields:** `["OBJECTID", "name", "county", "location", "numramps", "fee", "lights", "courdock", "parking", "restr", "latitude", "longitude"]`

