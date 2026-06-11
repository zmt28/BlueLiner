/**
 * M0 offline smoke test — IndexedDB byte-range cache for PMTiles + a viewport
 * prefetch. Proves we can store map tiles and render the vector base with the
 * network cut (especially on iOS Safari) BEFORE building the full Phase-2
 * download flow. Behind the "Cache this view" button (controls.ts) and the
 * window.blOffline debug API.
 *
 * Why byte-range level (offset+length) rather than per-tile: the PMTiles reader
 * fetches the header and directories the same way it fetches tiles -- as byte
 * ranges. Caching every range means an offline COLD START works (the reader can
 * load the header + dirs from cache on the next launch), not just warm tiles.
 *
 * The big basemap.pmtiles archive lives cross-origin on R2; the service worker
 * deliberately does NOT cache it (range/206 responses + size). This module is
 * how its bytes get persisted. The small assets (style.json, sprite, glyphs)
 * are handled by the service worker instead.
 */

import { PMTiles, FetchSource, type Source, type RangeResponse } from "pmtiles";

const DB_NAME = "bl-offline-tiles";
const STORE = "ranges";
const META = "meta";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 2);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
      if (!req.result.objectStoreNames.contains(META)) req.result.createObjectStore(META);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbGet(db: IDBDatabase, key: string): Promise<ArrayBuffer | undefined> {
  return new Promise((resolve, reject) => {
    const r = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
    r.onsuccess = () => resolve(r.result as ArrayBuffer | undefined);
    r.onerror = () => reject(r.error);
  });
}

function idbPut(db: IDBDatabase, key: string, val: ArrayBuffer): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(val, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

let _db: Promise<IDBDatabase> | null = null;
function db(): Promise<IDBDatabase> {
  return (_db ??= openDb());
}

// Writes to the cache happen ONLY while an explicit download is running, so the
// offline cache holds deliberately-downloaded areas -- not everything the user
// panned over online. Reads always check the cache (that's what serves offline).
let _persisting = false;
export function setPersisting(on: boolean): void {
  _persisting = on;
}

/**
 * A PMTiles Source that reads through the IndexedDB range cache: a cache hit
 * skips the network entirely (offline), a miss fetches from the wrapped remote
 * source and persists the bytes. Keyed by archive key + offset + length.
 */
class CachingSource implements Source {
  constructor(private readonly inner: Source) {}

  getKey(): string {
    return this.inner.getKey();
  }

  async getBytes(
    offset: number,
    length: number,
    signal?: AbortSignal,
    etag?: string,
  ): Promise<RangeResponse> {
    const key = `${this.inner.getKey()}|${offset}|${length}`;
    try {
      const hit = await idbGet(await db(), key);
      if (hit) return { data: hit };
    } catch (_) {
      /* IndexedDB unavailable -> fall through to network */
    }
    const resp = await this.inner.getBytes(offset, length, signal, etag);
    if (_persisting) {
      try {
        await idbPut(await db(), key, resp.data);
      } catch (_) {
        /* best-effort persist; rendering still works online */
      }
    }
    return resp;
  }
}

/** A PMTiles instance for `httpsUrl` whose byte reads persist to IndexedDB. */
export function cachingPmtiles(httpsUrl: string): PMTiles {
  return new PMTiles(new CachingSource(new FetchSource(httpsUrl)));
}

// -- viewport prefetch ----------------------------------------------

export interface BBox {
  w: number;
  s: number;
  e: number;
  n: number;
}

function lon2x(lon: number, z: number): number {
  return Math.floor(((lon + 180) / 360) * 2 ** z);
}
function lat2y(lat: number, z: number): number {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z);
}

/** Number of tiles a prefetch over bbox × [minZoom..maxZoom] would touch. */
export function tileCount(bbox: BBox, minZoom: number, maxZoom: number): number {
  let n = 0;
  for (let z = minZoom; z <= maxZoom; z++) {
    const nx = Math.abs(lon2x(bbox.e, z) - lon2x(bbox.w, z)) + 1;
    const ny = Math.abs(lat2y(bbox.s, z) - lat2y(bbox.n, z)) + 1;
    n += nx * ny;
  }
  return n;
}

export interface PrefetchResult {
  requested: number;
  present: number;
  bytes: number;
  entries: number;
}

/**
 * Fetch every basemap tile (and the header/dirs they need) over bbox ×
 * [minZoom..maxZoom] into the IndexedDB cache. Sequential to stay polite on
 * mobile networks; sparse tiles (no data) are skipped.
 */
export async function prefetch(
  httpsUrl: string,
  bbox: BBox,
  minZoom: number,
  maxZoom: number,
  onProgress?: (done: number, total: number) => void,
): Promise<PrefetchResult> {
  const total = tileCount(bbox, minZoom, maxZoom);
  const p = cachingPmtiles(httpsUrl);
  let requested = 0;
  let present = 0;
  for (let z = minZoom; z <= maxZoom; z++) {
    const x0 = lon2x(bbox.w, z);
    const x1 = lon2x(bbox.e, z);
    const y0 = lat2y(bbox.n, z);
    const y1 = lat2y(bbox.s, z);
    for (let x = Math.min(x0, x1); x <= Math.max(x0, x1); x++) {
      for (let y = Math.min(y0, y1); y <= Math.max(y0, y1); y++) {
        requested++;
        try {
          const r = await p.getZxy(z, x, y);
          if (r) present++;
        } catch (_) {
          /* skip individual tile failures */
        }
        if (onProgress) onProgress(requested, total);
      }
    }
  }
  const stats = await offlineStats();
  return { requested, present, ...stats };
}

/** Bytes + entry count currently held in the range cache. */
export async function offlineStats(): Promise<{ bytes: number; entries: number }> {
  try {
    const d = await db();
    return await new Promise((resolve, reject) => {
      const store = d.transaction(STORE, "readonly").objectStore(STORE);
      let bytes = 0;
      let entries = 0;
      const cur = store.openCursor();
      cur.onsuccess = () => {
        const c = cur.result;
        if (!c) return resolve({ bytes, entries });
        bytes += (c.value as ArrayBuffer).byteLength;
        entries++;
        c.continue();
      };
      cur.onerror = () => reject(cur.error);
    });
  } catch (_) {
    return { bytes: 0, entries: 0 };
  }
}

/** Drop all offline data (cached tile ranges + download log). */
export async function clearOffline(): Promise<void> {
  const d = await db();
  await new Promise<void>((resolve, reject) => {
    const tx = d.transaction([STORE, META], "readwrite");
    tx.objectStore(STORE).clear();
    tx.objectStore(META).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/** Ask the browser to keep our storage from being evicted under pressure. */
export async function requestPersist(): Promise<boolean> {
  try {
    if (navigator.storage?.persist) return await navigator.storage.persist();
  } catch (_) {
    /* not supported */
  }
  return false;
}

// -- download log (meta store) --------------------------------------

export interface OfflineMeta {
  downloads: number;
  lastAt: number | null; // epoch ms of the last successful download
}

function metaGet(d: IDBDatabase, key: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const r = d.transaction(META, "readonly").objectStore(META).get(key);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
function metaPut(d: IDBDatabase, key: string, val: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = d.transaction(META, "readwrite");
    tx.objectStore(META).put(val, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function offlineMeta(): Promise<OfflineMeta> {
  try {
    const m = (await metaGet(await db(), "summary")) as OfflineMeta | undefined;
    return m ?? { downloads: 0, lastAt: null };
  } catch (_) {
    return { downloads: 0, lastAt: null };
  }
}

// -- per-archive zoom range (read once from the PMTiles header) ------

const _zoomRange = new Map<string, { minZoom: number; maxZoom: number }>();
export async function zoomRange(httpsUrl: string): Promise<{ minZoom: number; maxZoom: number }> {
  const cached = _zoomRange.get(httpsUrl);
  if (cached) return cached;
  const h = await cachingPmtiles(httpsUrl).getHeader();
  const r = { minZoom: h.minZoom, maxZoom: h.maxZoom };
  _zoomRange.set(httpsUrl, r);
  return r;
}

// -- deterministic asset caching (style + sprite + glyphs) ----------
// Small, cross-origin under /basemap/: fetching them lets the service worker
// (stale-while-revalidate) persist them, so the vector base + its labels/icons
// load offline -- rather than relying on whatever happened to render online.

const GLYPH_RANGES = ["0-255", "256-511", "8192-8447"]; // basic Latin + accents + punctuation

export async function cacheAssets(styleUrl: string): Promise<number> {
  let n = 0;
  const touch = async (u: string): Promise<void> => {
    try {
      await fetch(u);
      n++;
    } catch (_) {
      /* best-effort */
    }
  };
  await touch(styleUrl);
  let style: {
    sprite?: string;
    glyphs?: string;
    layers?: { layout?: { "text-font"?: unknown } }[];
  };
  try {
    style = await (await fetch(styleUrl)).json();
  } catch (_) {
    return n;
  }
  if (typeof style.sprite === "string") {
    for (const suffix of [".json", ".png", "@2x.png", "@2x.json"]) await touch(style.sprite + suffix);
  }
  if (typeof style.glyphs === "string") {
    const stacks = new Set<string>();
    for (const layer of style.layers ?? []) {
      const f = layer?.layout?.["text-font"];
      if (Array.isArray(f) && f.every((x) => typeof x === "string")) stacks.add((f as string[]).join(","));
    }
    if (stacks.size === 0) stacks.add("Noto Sans Regular");
    for (const stack of stacks) {
      for (const range of GLYPH_RANGES) {
        await touch(
          style.glyphs.replace("{fontstack}", encodeURIComponent(stack)).replace("{range}", range),
        );
      }
    }
  }
  return n;
}

// -- download a viewport area across one or more archives -----------

export interface Archive {
  url: string;
  label: string;
}
export interface DownloadProgress {
  phase: "assets" | "tiles";
  done: number;
  total: number;
}

interface ArchivePlan {
  url: string;
  minZ: number;
  maxZ: number;
  total: number;
}

/** Clamp [viewZoom-1 .. viewZoom+2] to each archive's own zoom range. */
async function planArea(bbox: BBox, archives: Archive[], viewZoom: number): Promise<ArchivePlan[]> {
  const plans: ArchivePlan[] = [];
  for (const a of archives) {
    const zr = await zoomRange(a.url);
    const minZ = Math.max(zr.minZoom, Math.min(zr.maxZoom, viewZoom - 1));
    const maxZ = Math.min(zr.maxZoom, viewZoom + 2);
    if (maxZ >= minZ) plans.push({ url: a.url, minZ, maxZ, total: tileCount(bbox, minZ, maxZ) });
  }
  return plans;
}

/** How many tiles a download of this area would touch (for a pre-flight cap). */
export async function estimateArea(
  bbox: BBox,
  archives: Archive[],
  viewZoom: number,
): Promise<number> {
  return (await planArea(bbox, archives, viewZoom)).reduce((s, p) => s + p.total, 0);
}

/**
 * Download a viewport area for offline use: cache the basemap assets, then
 * prefetch every archive's tiles over the bbox (zoom-clamped per archive).
 * Cumulative -- downloading another area extends coverage.
 */
export async function downloadArea(
  bbox: BBox,
  archives: Archive[],
  viewZoom: number,
  styleUrl: string | null,
  onProgress?: (p: DownloadProgress) => void,
): Promise<{ tiles: number; bytes: number; entries: number }> {
  await requestPersist();
  const plans = await planArea(bbox, archives, viewZoom);
  const grand = plans.reduce((s, p) => s + p.total, 0);
  let done = 0;
  let tiles = 0;

  setPersisting(true);
  try {
    if (styleUrl) {
      onProgress?.({ phase: "assets", done, total: grand });
      await cacheAssets(styleUrl);
    }
    for (const plan of plans) {
      const r = await prefetch(plan.url, bbox, plan.minZ, plan.maxZ, () => {
        done++;
        onProgress?.({ phase: "tiles", done, total: grand });
      });
      tiles += r.present;
    }
  } finally {
    setPersisting(false);
  }

  try {
    const meta = await offlineMeta();
    await metaPut(await db(), "summary", { downloads: meta.downloads + 1, lastAt: Date.now() });
  } catch (_) {
    /* meta is best-effort */
  }
  const stats = await offlineStats();
  return { tiles, bytes: stats.bytes, entries: stats.entries };
}

// Debug handle for manual testing (e.g. from a desktop console).
declare global {
  interface Window {
    blOffline?: {
      prefetch: typeof prefetch;
      downloadArea: typeof downloadArea;
      estimateArea: typeof estimateArea;
      stats: typeof offlineStats;
      meta: typeof offlineMeta;
      clear: typeof clearOffline;
      persist: typeof requestPersist;
    };
  }
}
window.blOffline = {
  prefetch,
  downloadArea,
  estimateArea,
  stats: offlineStats,
  meta: offlineMeta,
  clear: clearOffline,
  persist: requestPersist,
};
