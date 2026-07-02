/**
 * Blueliner frontend type definitions.
 *
 * These mirror the actual JSON shapes returned by main.py endpoints
 * (see _assemble_rivers, api_states, api_pins, etc.). Authored from
 * inspection of the Python response builders; conservative on optional
 * vs required fields -- when in doubt, marked optional.
 *
 * Ambient (no top-level imports/exports) so every .js / .ts file picks
 * these up automatically via the tsconfig include glob.
 */

// ---------------------------------------------------------------------------
// Conditions / scoring
// ---------------------------------------------------------------------------

/** Discrete condition score; matches SCORE_COLORS / SCORE_LABELS keys. */
type ConditionKey = "green" | "yellow" | "red" | "gray";

/** Design-system condition variant suffix (post-COND_VARIANT mapping). */
type ConditionVariant = "good" | "fair" | "poor" | "none";

/** One reading from USGS (temp, discharge, gauge height, ...). */
interface GaugeReading {
  /** Human description of the variable, e.g. "Streamflow, ft3/s". */
  variable: string;
  /** Stringified numeric value (USGS returns strings; the JS parses). */
  value: string;
  /** Optional unit code if the server pulled one. */
  unit?: string;
  /** ISO timestamp of when this reading was observed. */
  dateTime?: string;
}

interface GaugeConditions {
  overall: ConditionKey;
  temp?: ConditionKey;
  flow?: ConditionKey;
  current_flow?: number;
  temp_f?: number;
}

interface Gauge {
  site_name: string;
  site_no: string | null;
  variables: GaugeReading[];
  conditions: GaugeConditions;
  historical_median: number | null;
}

/** A gauge point for on-map rendering: the USGS site's own location +
    its condition. Trimmed from the full Gauge (the river panel's
    server-rendered popup_html keeps the rest). One condition icon is
    drawn per GaugePoint. */
interface GaugePoint {
  lat: number;
  lon: number;
  site_no: string | null;
  site_name: string;
  conditions: { overall: ConditionKey };
}

/** A river in the /api/rivers response. Server-assembled from one or
    more gauges + NHD/NLDI/trout-stream context. */
interface River {
  name: string;
  lat: number;
  lon: number;
  /** Composite site_no, null if no gauge contributes a number. */
  site_no: string | null;
  conditions: { overall: ConditionKey };
  /** Hex color string (post-PR-2: muted moss/ochre/clay/stone). */
  color: string;
  label: string;
  on_trout: boolean;
  near_stocked: boolean;
  hatch_zone: string;
  active_hatches: string[];
  /** NHD levelpath IDs the gauges sit on; used to match clickable-
      stream reaches by network identity, not just GNIS name. */
  levelpathids: number[];
  /** Pre-rendered HTML for the river-detail panel body. */
  popup_html: string;
  /** Per-gauge points (location + condition) -- one condition icon each. */
  gauges: GaugePoint[];
  active?: unknown[];
  stocked_waters?: unknown[];
  access_count?: number;
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

interface ApiState {
  /** Two-letter postal code, e.g. "MD". */
  code: string;
  name: string;
  /** [lat, lng] -- the order app.js feeds into Leaflet's setView. */
  center: [number, number];
}

// ---------------------------------------------------------------------------
// Pins (saved user markers)
// ---------------------------------------------------------------------------

interface Pin {
  id: number;
  lat: number;
  lon: number;
  note: string;
  /** ISO timestamp, server-set on add. */
  created_at: string;
}

// ---------------------------------------------------------------------------
// Catches (catch log)
// ---------------------------------------------------------------------------

/** Server-built environmental snapshot attached to a catch. */
interface CatchEnv {
  flow_cfs?: number | null;
  water_temp_f?: number | null;
  air_temp_f?: number | null;
  conditions?: ConditionKey;
  /** Other gauge readings the server decided to keep. */
  [key: string]: unknown;
}

interface Catch {
  id: number;
  user_id: number;
  created_at: string;
  occurred_at: string;
  river_name: string | null;
  river_site_no: string | null;
  lat: number | null;
  lon: number | null;
  species: string | null;
  length_in: number | null;
  fly_used: string | null;
  notes: string | null;
  visibility: "private" | "public";
  env: CatchEnv | null;
}

/** Enrichment-preview response (no insert): conditions for a lat/lon
    pair the user is about to log. */
interface CatchEnrichment extends CatchEnv {
  river_name?: string | null;
  river_site_no?: string | null;
}

// ---------------------------------------------------------------------------
// GeoJSON layer features (clickable streams, trout, access, public lands,
// river lines). All come back as standard GeoJSON FeatureCollections.
// ---------------------------------------------------------------------------

interface GeoJsonFeatureCollection<P = Record<string, unknown>, G = GeoJsonGeometry> {
  type: "FeatureCollection";
  features: GeoJsonFeature<P, G>[];
}

interface GeoJsonFeature<P = Record<string, unknown>, G = GeoJsonGeometry> {
  type: "Feature";
  geometry: G;
  properties: P;
  id?: string | number;
}

type GeoJsonGeometry =
  | { type: "Point"; coordinates: [number, number] }
  | { type: "LineString"; coordinates: [number, number][] }
  | { type: "MultiLineString"; coordinates: [number, number][][] }
  | { type: "Polygon"; coordinates: [number, number][][] }
  | { type: "MultiPolygon"; coordinates: [number, number][][][] };

/** /api/trout feature properties. Different states use different
    column names; the app tries each in turn. */
interface TroutFeatureProps {
  NAME?: string;
  GNIS_Name?: string;
  STream_Nam?: string;
  [key: string]: unknown;
}

/** /api/clickable_streams feature properties. */
interface ClickableStreamProps {
  gnis_name?: string | null;
  levelpathid?: number;
  /** NHDPlus COMID of the reach; anchors the elevation-profile lookup. */
  comid?: number;
  /** Raw per-state agency designation baked into the tiles, e.g. "class_a",
      "wilderness", "wild_reproduction", "stocked", "designated", or null. The
      client names it on the reach card; coloring is by `tier`. */
  trout_class?: string | null;
  /** Nationwide quality tier (the color axis): "gold" | "class1" | "class2" |
      "class3" | null. Derived in build_clickable_streams via trout_registry. */
  tier?: string | null;
  /** Filter flags: naturally-reproducing wild trout present / native species. */
  is_wild?: boolean;
  is_native?: boolean;
  streamorder?: number;
  [key: string]: unknown;
}

/** /api/reach_detail response: context for an ungauged reach the user
    clicked (hatches for its zone + nearby access/stocking). Conditions are
    omitted -- the card shows a note since there's no gauge on the reach. */
interface ReachHatchEntry {
  common_name?: string;
  insect?: string;
  hook_sizes?: string;
  time_of_day?: string;
  patterns?: string[];
}
interface ReachAccessEntry {
  name?: string;
  type?: string;
  access?: string;
  notes?: string | null;
  agency_url?: string | null;
}
interface ReachStockedEntry {
  water?: string;
  species?: string[];
  category?: string;
  agency_url?: string | null;
}
interface ReachDetail {
  hatch: { zone?: string | null; active: ReachHatchEntry[] };
  access: ReachAccessEntry[];
  stocked: ReachStockedEntry[];
  /** River-level trout designation for the clicked reach's levelpath
      group (strongest class on ANY flowline of the river; name fallback
      when the reach has no levelpathid). Null class = no evidence. */
  trout?: {
    river_class?: string | null;
    river_label?: string | null;
  };
}

/** /api/elevation_profile response: the NHDPlus-derived gradient profile
    for the named river section containing a clicked reach. */
interface ElevationProfile {
  name: string;
  length_mi: number;
  elev_change_ft: number;
  high_ft: number;
  low_ft: number;
  grade_ft_per_mi: number;
  grade_pct: number;
  grade_deg: number;
  reach_count: number;
  /** Profile polyline: distance (mi) vs elevation (ft), upstream-first. */
  points: Array<{ d: number; e: number }>;
  /** The clicked reach's position on the axis, for the cursor marker. */
  focus: { d: number; e: number } | null;
}

/** Nationwide quality tier -- the stream color axis. The raw per-state
    `trout_class` designations are normalized to one of these in the build
    (gold / class1 / class2 / class3), or "unclassified" when no tier applies. */
type StreamTier = "gold" | "class1" | "class2" | "class3" | "unclassified";

/** Stream-network filters layered over the tier coloring: which quality tiers
 *  to show, plus the orthogonal wild / native narrowing. */
interface StreamFilters {
  wild: boolean;
  native: boolean;
  tiers: Record<StreamTier, boolean>;
}

/** /api/stocking feature properties (one per stocked water). */
interface StockedFeatureProps {
  water?: string;
  species?: string[];
  category?: string;
  /** Pre-formatted season label, e.g. "Year-round" or "Mar–Jun". */
  season?: string;
  agency_url?: string | null;
  source?: "baseline" | "live" | string;
  [key: string]: unknown;
}

/** /api/access feature properties. */
type AccessType = "boat_ramp" | "fishing_access" | "pier" | "parking" | string;

interface AccessFeatureProps {
  name?: string;
  type?: AccessType;
  agency_url?: string | null;
  notes?: string | null;
  access?: "public" | "private" | "permit" | "fee" | "private_easement" | string;
  /** National river-POI overlay provenance. */
  source?: "osm" | "ridb" | "agency" | string;
  /** How the coordinate was sourced: surveyed (agency/RIDB) vs mapped (OSM). */
  precision?: "surveyed" | "mapped" | string;
  source_id?: string | null;
  levelpathid?: number | string | null;
  /** Containing PAD-US public-land unit ("Gunpowder Falls SP"), if any. */
  park?: string | null;
  [key: string]: unknown;
}

/** /api/dams feature properties (USACE National Inventory of Dams). */
interface DamFeatureProps {
  name?: string;
  river?: string | null;
  owner?: string | null;
  city?: string | null;
  purposes?: string | null;
  height_ft?: number | null;
  year?: string | null;
  nid_id?: string | null;
  agency_url?: string | null;
  [key: string]: unknown;
}

/** Trails PMTiles feature properties (USGS National Digital Trails). */
interface TrailProps {
  name?: string;
  trail_type?: string;
  surface?: string;
  length_mi?: number;
  [key: string]: unknown;
}

/** /api/public_lands feature properties. */
type PublicAccessTier = "OA" | "RA" | "XA" | "UK";

interface PublicLandsProps {
  unit_name?: string;
  manager?: string;
  designation?: string;
  public_access?: PublicAccessTier;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// History (gauge sparkline)
// ---------------------------------------------------------------------------

interface HistorySeries {
  parameter?: string;
  unit?: string;
  points: Array<{ date: string; value: number | null }>;
}

interface HistoryResponse {
  series: HistorySeries[];
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

interface AuthMe {
  email?: string;
  user_id?: number;
  /** Other server-attached profile fields. */
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Layer-preference persistence (bl_layers localStorage)
// ---------------------------------------------------------------------------

/** Map from layer-toggle id (e.g. "lyr-fishable") to whether it's on. */
type LayerPrefs = Record<string, boolean>;

// (PR B2 removed Leaflet. MapLibre GL JS ships its own types and is
// imported as a module, so there's no ambient map-library augmentation
// here anymore. River identity for map features flows via the
// site_no -> River registry in map-setup.ts, not a layer property.)

// ---------------------------------------------------------------------------
// Lucide (CDN-loaded global)
// ---------------------------------------------------------------------------

interface LucideAPI {
  createIcons(opts?: { nameAttr?: string; attrs?: Record<string, string> }): void;
}

interface Window {
  lucide?: LucideAPI;
}
