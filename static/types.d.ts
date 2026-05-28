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
  /** Optional: server-attached convenience fields used by the client. */
  gauges?: Gauge[];
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

/** /api/river_lines + /api/river_geom feature properties. */
interface RiverLineProps {
  site_no?: string;
  color?: string;
}

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
  trout_class?: number | null;
  streamorder?: number;
  [key: string]: unknown;
}

/** /api/access feature properties. */
type AccessType = "boat_ramp" | "walk_in" | "pier" | "parking" | string;

interface AccessFeatureProps {
  name?: string;
  type?: AccessType;
  agency_url?: string | null;
  notes?: string | null;
  access?: "public" | "permit" | "fee" | "private_easement" | string;
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

// ---------------------------------------------------------------------------
// Leaflet declaration-merging
// The app attaches `_blRiver` to markers + GeoJSON layers so a click
// handler can recover the parent River from the layer. Augment the
// Leaflet types so TS doesn't flag the access pattern.
// ---------------------------------------------------------------------------

declare module "leaflet" {
  interface Marker {
    _blRiver?: River;
  }
  interface GeoJSON {
    _blRiver?: River;
  }
  interface Layer {
    _blRiver?: River;
  }
}

// ---------------------------------------------------------------------------
// Lucide (CDN-loaded global)
// ---------------------------------------------------------------------------

interface LucideAPI {
  createIcons(opts?: { nameAttr?: string; attrs?: Record<string, string> }): void;
}

interface Window {
  lucide?: LucideAPI;
}
