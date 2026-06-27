# Endpoint watch

_Run: 2026-06-27 19:13 UTC -- 15/15 reachable, **7 READY TO PROMOTE**._

| id | state | kind | status | captured |
|----|-------|------|--------|----------|
| md-fisheries-folder | MD | discover | UP | yes |
| nv-lct-successor | NV | discover | UP | yes |
| vt-stocked-trout-line | VT | discover | UP | yes |
| vt-fish-stocking-points-schema | VT | field_dump | UP | yes |
| ma-stocked-trout-discover | MA | discover | UP | yes |
| wv-wild-trout-discover | WV | discover | UP | yes |
| ca-trout-only-stocked-discover | CA | discover | UP | yes |
| ut-trout-streams-beyond-blueribbon | UT | discover | UP | yes |
| cand-access_points-de-dnrec-public-fishing-ponds | DE | verify | UP | PROMOTE |
| cand-access_points-ca-cdfw-fishing-guide-boating-facilities | CA | verify | UP | PROMOTE |
| cand-access_points-ut-ut-dwr-community-fisheries | UT | verify | UP | PROMOTE |
| cand-access_points-tx-tx-public-water-access---boat-ramps--owner-uncon | TX | verify | UP | PROMOTE |
| cand-access_points-sd-sd-boat-ramps--not-authoritative----personal-ago | SD | verify | UP | PROMOTE |
| cand-access_points-nc-ncwrc-boating-access-areas--inland | NC | verify | UP | PROMOTE |
| cand-access_points-al-al-boat-ramps--third-party-national-org | AL | verify | UP | PROMOTE |

## Captured detail (reachable entries)

### md-fisheries-folder (MD / discover)
> MD now classifies via DesignatedUse_Trout Des_Use (Use III/III-P wild vs IV/IV-P stocked), so the core wild/stocked split is solved. Lower priority: enumerate MD DNR Fisheries for an even richer wild-trout / special-management (catch-and-release / trophy) layer that could add a finer tier.

- **services / layers matching fish/trout:**
  - `Fisheries/AnadromousFish` (FeatureServer)
  - `Fisheries/AnadromousFish` (MapServer)
  - `Fisheries/BenthicColdwaterObligates` (FeatureServer)
  - `Fisheries/BenthicColdwaterObligates` (MapServer)
  - `Fisheries/ChesBaySandAndMudGISService` (FeatureServer)
  - `Fisheries/ChesBaySandAndMudGISService` (MapServer)
  - `Fisheries/ColdwaterResourceMapData` (FeatureServer)
  - `Fisheries/ColdwaterResourceMapData` (MapServer)
  - `Fisheries/DesignatedUse_Trout` (MapServer)
  - `Fisheries/FinfishDiagnostics` (FeatureServer)
  - `Fisheries/FinfishDiagnostics` (MapServer)
  - `Fisheries/FisheriesLULC50mtrSW` (MapServer)
  - `Fisheries/FishHatcheries` (MapServer)
  - `Fisheries/FishingGrounds` (FeatureServer)
  - `Fisheries/FishingGrounds` (MapServer)
  - `Fisheries/GearAreaPoints` (MapServer)
  - `Fisheries/GearAreasAndSanctuaries` (MapServer)
  - `Fisheries/GearAreasSimple` (MapServer)
  - `Fisheries/HOBOWaterTempLoggers` (MapServer)
  - `Fisheries/HSCConfirmed` (FeatureServer)
  - `Fisheries/HSCConfirmed` (MapServer)
  - `Fisheries/HSC` (FeatureServer)
  - `Fisheries/HSC` (MapServer)
  - `Fisheries/ImpoundmentSurveys` (MapServer)
  - `Fisheries/InvasiveSpecies` (MapServer)
  - `Fisheries/MDSmallPondsSrvc` (FeatureServer)
  - `Fisheries/MDSmallPondsSrvc` (MapServer)
  - `Fisheries/NOAACodesShellfish` (MapServer)
  - `Fisheries/PotomacSurveys` (MapServer)
  - `Fisheries/PublicFishingAccessSites` (FeatureServer)
  - `Fisheries/PublicFishingAccessSites` (MapServer)
  - `Fisheries/StreamSurveys_NoTrout` (MapServer)
  - `Fisheries/TidalBass` (FeatureServer)
  - `Fisheries/TidalBass` (MapServer)
  - `Fisheries/TroutPopulation_Watershed2017` (MapServer)
  - `Fisheries/TroutStockingActivities` (MapServer)

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

### vt-stocked-trout-line (VT / discover)
> VT trout = all-wild Brook Trout Waters (layer 49, EBTJV catchment polygons). Seeking a STOCKED-trout LINE layer to add a stocked dimension (the NJ/VA wild-first two-source pattern). Only point 'Fish Stocking Locations (2021)' (layer 52, all-species points) found so far -- points buffer poorly onto the NHD line network. Re-list this MapServer's fish/trout sublayers each run to catch a stocked-stream line layer if VT publishes one, and re-scan the map_services folder for a new stocking service.

- **matching layers:**
  - layer 38: `Commercial Baitfish Dealers` (esriGeometryPoint)
  - layer 51: `Fish and Wildlife Facilities` (esriGeometryPoint)
  - layer 52: `Fish Stocking Locations (2021)` (esriGeometryPoint)
  - layer 53: `Family Friendly Fishing` (esriGeometryPoint)
  - layer 54: `Chittenden County Fishing` (esriGeometryPoint)
  - layer 2: `Fishing Access Areas` (esriGeometryPoint)
  - layer 5: `Invasive Plant Atlas` (esriGeometryPoint)
  - layer 45: `Baitfish Zones` (esriGeometryPolygon)
  - layer 49: `Brook Trout Waters` (esriGeometryPolygon)
  - layer 34: `Fisheries Admin Districts` (esriGeometryPolygon)

### vt-fish-stocking-points-schema (VT / field_dump)
> Capture schema + samples of 'Fish Stocking Locations (2021)'. If it carries a species field we can filter to trout (brook/brown/rainbow) and buffer the points as a COARSE stocked fallback for VT -- lower fidelity than a line layer, but better than no stocked dimension. Confirm geometry (expected point) and whether a waterbody/reach-name field exists to anchor points onto named flowlines.

- **layer:** `Fish Stocking Locations (2021)`  geometry: `esriGeometryPoint`  count: `250`
- **fields** (name | type | alias | coded-domain):
  - `OBJECTID` | OID | 'OBJECTID'
  - `Hatchery` | String | 'Hatchery'
  - `Watershed` | String | 'Watershed'
  - `Waterbody` | String | 'Waterbody'
  - `Location` | String | 'Location'
  - `Town` | String | 'Town'
  - `Species` | String | 'Species'
  - `LAT` | Double | 'LAT'
  - `LONG` | Double | 'LONG'
  - `SHAPE` | Geometry | 'SHAPE'
- **sample features (3):**
  - ```json
    {"OBJECTID": 1, "Hatchery": "Bennington", "Watershed": "Deerfield River", "Waterbody": "Sherman Reservoir", "Location": "Access area", "Town": "Whitingham", "Species": "BNT", "LAT": 42.745277, "LONG": -72.926788}
    ```
  - ```json
    {"OBJECTID": 2, "Hatchery": "Bennington", "Watershed": "Deerfield River", "Waterbody": "Lake Raponda", "Location": "Access area", "Town": "Wilmington", "Species": "RBT", "LAT": 42.884006, "LONG": -72.820045}
    ```
  - ```json
    {"OBJECTID": 3, "Hatchery": "Bennington", "Watershed": "Deerfield River", "Waterbody": "Red Mill Pond", "Location": "Access area", "Town": "Woodford", "Species": "BKT", "LAT": 42.890621, "LONG": -73.028102}
    ```

### ma-stocked-trout-discover (MA / discover)
> MA trout = all-wild Coldwater Fisheries Resources (AGOL/DFW_CFR). Seeking a MassWildlife STOCKED-trout layer to add a stocked dimension. The AGOL folder holds only DFW_CFR + DiadromousFish; the DFG (Dept of Fish & Game), FWE and NHESP folders expose no fish/trout-NAMED services. Enumerate every folder for a trout-stocking layer; MassWildlife's stocked-trout list may be an AGOL item or PDF rather than a server layer -- flag if no GIS layer exists so we fall back to a curated baseline.

- **services / layers matching fish/trout:**
  - _folder_ `AGOL`
  - _folder_ `Basemaps`
  - _folder_ `DCAM`
  - _folder_ `DCR`
  - _folder_ `DEP`
  - _folder_ `DFG`
  - _folder_ `DHCD`
  - _folder_ `DOEServices`
  - _folder_ `DPH`
  - _folder_ `DPS`
  - _folder_ `DUA`
  - _folder_ `EOLWD`
  - _folder_ `EPSServices`
  - _folder_ `FEMA`
  - _folder_ `FWE`
  - _folder_ `GeocodeServices`
  - _folder_ `GeocodeServicesArcMap`
  - _folder_ `HealthConnector`
  - _folder_ `Legislature`
  - _folder_ `LiDAR`
  - _folder_ `MEMA`
  - _folder_ `NHESP`
  - _folder_ `PublicSafety`
  - _folder_ `Transportation`
  - _folder_ `Utilities`

### wv-wild-trout-discover (WV / discover)
> WV trout = all-stocked (dnrRec_fishing layer 4 Stocked Trout Streams); wild brook trout currently come ONLY from the range-wide EBTJV native overlay. Ruled out in dnrRec_fishing: 'Special Regulation Areas' layers 13/14 are 'Zero Possession Non-Game Fish' (not trout) and layer 5 is warmwater. Enumerate WVGIS / WV DNR services + AGOL for a wild / native brook trout / catch-and-release trout layer to add a WV-specific wild source on top of EBTJV.

- **services / layers matching fish/trout:**
  - _folder_ `Applications`
  - _folder_ `Biota`
  - _folder_ `Boundaries`
  - _folder_ `Climatology_Meteorology_Atmosphere`
  - _folder_ `Economy`
  - _folder_ `Elevation`
  - _folder_ `Environment`
  - _folder_ `Farming`
  - _folder_ `Geocode`
  - _folder_ `Geoscientific_Information`
  - _folder_ `Hazards`
  - _folder_ `Health`
  - _folder_ `Imagery_BaseMaps_EarthCover`
  - _folder_ `Inland_Waters`
  - _folder_ `Intelligence_Military`
  - _folder_ `Location`
  - _folder_ `Planning_Cadastre`
  - _folder_ `Society`
  - _folder_ `Structure`
  - _folder_ `Test`
  - _folder_ `Transportation`
  - _folder_ `Utilities`
  - _folder_ `Utilities_Communication`

### ca-trout-only-stocked-discover (CA / discover)
> CA = wild-only (Heritage & Wild Trout, biosds1356). Seeking a TROUT-ONLY stocked layer to add a stocked dimension (NJ/VA wild-first pattern). CDFW 'Recent Stocked Waters' [biosds778_fpu, 536 polygons] is the only stocked layer found but is ALL-SPECIES (fields are just StockingWaterID/DFGWATERID/Counties/last_yr_stkd -- no species/taxa), so whole-layer use would mislabel warmwater reservoirs as stocked trout. Re-scan the CDFW org + AGOL for a catchable-TROUT planting / hatchery trout allotment layer carrying a species field; if none surfaces, revisit ds778 with a coldwater/elevation or NHD-COMID filter.

- **services / layers matching fish/trout:**
  - `20mm_Fish_Distribution` (FeatureServer)
  - `California_Department_of_Fish_and_Wildlife_Terrestrial_Regions` (FeatureServer)
  - `CDFW_Fish_Hatcheries` (FeatureServer)
  - `CDFW_Fish_Rescues` (FeatureServer)
  - `Colorado_River_Access` (FeatureServer)
  - `Essential_Fish_Habitat_Conservation_Area` (FeatureServer)
  - `Fish_Passage_updated_11_21` (FeatureServer)
  - `Fishing_Closures` (FeatureServer)
  - `FishingClosure` (FeatureServer)
  - `FishingRegulationsV1` (FeatureServer)
  - `Groundfish_Conservation_Area` (FeatureServer)
  - `Lake_Tahoe_Fish_Habitat` (FeatureServer)
  - `Local_Fish_Passage_Recommendation_update` (FeatureServer)
  - `Los_Padres_NF_Trail_and_Road_Access` (FeatureServer)
  - `PilotPlantingPlotsOutline` (FeatureServer)
  - `PilotPlantingRows` (FeatureServer)
  - `RAMP_2023_2024_Survey_Map_WFL8` (FeatureServer)
  - `RAMP_Fishing_Zones` (FeatureServer)
  - `RAMPFishingZones` (FeatureServer)
  - `regLinkFishing` (FeatureServer)
  - `regLinkFishingDev` (FeatureServer)
  - `SLS_Fish_Distribution` (FeatureServer)
  - `SLS_FishDistribution` (FeatureServer)
  - `ago_launch_20190614` (MapServer)
  - `FishingGuide` (FeatureServer)

### ut-trout-streams-beyond-blueribbon (UT / discover)
> UT = Blue Ribbon premier rivers only (gold) + native cutthroat (UTCT overlay). No comprehensive UT trout-STREAM classification found in the UDWR org (services/ZzrwjTRez6FJiOq4); the UDWR_Fish_Stocking_Events VIEW is LAKES (polygons), not streams. Re-scan for a statewide sportfish-management / trout-stream classification, or a stocked-trout STREAM layer, to add tiers + a stocked dimension beyond the ~17 Blue Ribbon segments.

- **services / layers matching fish/trout:**
  - `5_1a_Power_Plant_Capacity__Electricity` (FeatureServer)
  - `5_1b_Largest_Power_Plants_Electricity` (FeatureServer)
  - `accessPlanning_Surveys_base` (FeatureServer)
  - `accessPlanning_Surveys_edits` (FeatureServer)
  - `airboat_tracks` (FeatureServer)
  - `Biomass_Power_Plants_1_view` (FeatureServer)
  - `Bison_SITLA_Access` (FeatureServer)
  - `BlackBear_SITLA_Access` (FeatureServer)
  - `Blue_Ribbon_Fisheries_VIEW` (FeatureServer)
  - `BRCMP_BoaterAccess` (FeatureServer)
  - `BRCMP_Recreation_Access` (FeatureServer)
  - `BRCMP_Recreation_Access_Boater_Access_Points` (FeatureServer)
  - `BRCMP_Recreation_Access_Cutler_Reservoir_Recreation_Areas` (FeatureServer)
  - `BRCMP_Recreation_Access_DWR_Managed_Access_Areas` (FeatureServer)
  - `BRCMP_Recreation_Access_Trails` (FeatureServer)
  - `CABHS_SITLA_Access` (FeatureServer)
  - `Coal_Power_Plants_GIS_Layer` (FeatureServer)
  - `Coal_Power_Plants_view` (FeatureServer)
  - `Coal_Power_Plantsupdated` (FeatureServer)
  - `Coal_Power_Plantsupdated_view` (FeatureServer)
  - `Community_fisheries` (FeatureServer)
  - `Consumption_at_Power_Plants` (FeatureServer)
  - `Cutthroat_Genetic_Results` (FeatureServer)
  - `DesertBHS_SITLA_Access` (FeatureServer)
  - `Elk_Antlerless_SITLA_Access` (FeatureServer)
  - `Elk_SITLA_Access` (FeatureServer)
  - `Fishing__Loco` (FeatureServer)
  - `geothermal_power_plants_updated_view` (FeatureServer)
  - `Geothermal_Power_Plants_view` (FeatureServer)
  - `GSL_WMA_Access_Points` (FeatureServer)
  - `hydroelectric_power_plants_view` (FeatureServer)
  - `JRCMP_Access_JRC_Recreation_Planning_Lines` (FeatureServer)
  - `JRCMP_Access_JRC_Recreation_Planning_Points` (FeatureServer)
  - `JRCMP_Access_Trails` (FeatureServer)
  - `JRCMP_Hydrology_Waste_Water_Treatment_Plants` (FeatureServer)
  - `JRCMP_Infrastructure_Salt_Lake_County_Flood_Control_Access` (FeatureServer)
  - `JRCMP_Recreation_Fishing_Hotspots` (FeatureServer)
  - `Lake_Powell_Boater_Zip` (FeatureServer)
  - `Loconotive_Springs_WMA_Fishing___view` (FeatureServer)
  - `Moose_SITLA_Access` (FeatureServer)
  - `MtnGoat_SITLA_Access` (FeatureServer)
  - `MUDE_SITLA_Access` (FeatureServer)
  - `Natural_Gas_Power_Plants__view` (FeatureServer)
  - `Natural_gas_power_plants_updated_view` (FeatureServer)
  - `Natural_Gas_Processing_Plants_in_the_U_S__view` (FeatureServer)
  - `petroleum_power_plants_updated_view` (FeatureServer)
  - `Petroleum_Power_Plants_view` (FeatureServer)
  - `plantPortalV6_View` (FeatureServer)
  - `plantPortalV7_view` (FeatureServer)
  - `PlantZones` (FeatureServer)
  - `Pronghorn_SITLA_Access` (FeatureServer)
  - `Public_Access_Properties_Dashboards_` (FeatureServer)
  - `RESTRICT_20220718_USFS_FishlakeNF` (FeatureServer)
  - `restrict_20240712_USFS_Fishlake` (FeatureServer)
  - `Rich_County_Trail_Assets_and_Access_WFL1` (FeatureServer)
  - `RockyMtnBHS_SITLA_Access` (FeatureServer)
  - `RTP_Handicap_Access` (FeatureServer)
  - `solar_power_plants_view` (FeatureServer)
  - `Stockton_Bar_Line` (FeatureServer)
  - `Turkey_Release_Transplant_Areas_WFL1` (FeatureServer)
  - `Turkey_SITLA_Access` (FeatureServer)
  - `UDWR_Fish_Stocking_Events_1979_2024_VIEW` (FeatureServer)
  - `updated_solar_plants_12_19_25_update_view` (FeatureServer)
  - `USAC_Weber_River_access` (FeatureServer)
  - `Utah_Lake_Access` (FeatureServer)
  - `Utah_Natural_Gas_Processing_Plants` (FeatureServer)
  - `Utah_Natural_Gas_Processing_Plants_view` (FeatureServer)
  - `Weber_River_access_ruling` (FeatureServer)
  - `White_Sands_Launch_Complex` (FeatureServer)
  - `Wind_Power_Plants_view` (FeatureServer)
  - `Zip_Fish` (FeatureServer)

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

