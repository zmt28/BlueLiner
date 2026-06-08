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

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
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
    try {
      await idbPut(await db(), key, resp.data);
    } catch (_) {
      /* best-effort persist; rendering still works online */
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

/** Drop the whole range cache (test reset). */
export async function clearOffline(): Promise<void> {
  const d = await db();
  await new Promise<void>((resolve, reject) => {
    const tx = d.transaction(STORE, "readwrite");
    tx.objectStore(STORE).clear();
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

// Debug handle for manual testing (e.g. from a desktop console).
declare global {
  interface Window {
    blOffline?: {
      prefetch: typeof prefetch;
      stats: typeof offlineStats;
      clear: typeof clearOffline;
      persist: typeof requestPersist;
    };
  }
}
window.blOffline = {
  prefetch,
  stats: offlineStats,
  clear: clearOffline,
  persist: requestPersist,
};
