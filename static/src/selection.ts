/**
 * Centralized river selection. Single owner of "which gauged river is
 * selected" -- the three select paths (gauge-pin click in rivers.ts,
 * stream-line click in streams.ts, search-result select in search.ts)
 * all funnel through selectRiver(), and every panel close path (X, ESC,
 * map tap, snap-sheet drag-down) reaches clearRiverSelection() via
 * closeRiverPanel().
 *
 * An ungauged-reach card (streams.ts) is a stream selection, not a
 * river selection -- it never goes through here, so getSelectedRiver()
 * stays null for it.
 *
 * Cross-module dependencies:
 *   - openRiverPanel from ./river-panel (a leaf; closeRiverPanel
 *     reaches back via window.clearRiverSelection to avoid the import
 *     cycle, same pattern as window.clearStreamHighlight)
 *   - highlightStream from streams.ts via window -- streams.ts imports
 *     selectRiver, so a direct import here would be a cycle.
 */

import { openRiverPanel } from "./river-panel";

let _selectedRiver: River | null = null;

export function getSelectedRiver(): River | null {
  return _selectedRiver;
}

// -- Gauge-disc renderer (rivers.ts) -----------------------------------
// Condition discs render only for the selected river. rivers.ts owns
// the markers and registers its show/hide pair here (registration
// rather than an import: rivers.ts imports selectRiver, so importing
// rivers.ts back would be a cycle).

interface GaugeRenderer {
  show(river: River): void;
  hide(): void;
}

let _gaugeRenderer: GaugeRenderer | null = null;

export function registerGaugeRenderer(r: GaugeRenderer): void {
  _gaugeRenderer = r;
}

/**
 * Select a gauged river: open its detail panel and highlight its
 * reaches in the clickable network. `streamProps` carries the clicked
 * reach's identity when the selection came from a stream-line tap;
 * otherwise the river's own name / first levelpathid identify the
 * reaches to highlight.
 */
export function selectRiver(river: River, streamProps?: ClickableStreamProps): void {
  const changed = river !== _selectedRiver;
  _selectedRiver = river;
  openRiverPanel(river);
  window.highlightStream(
    streamProps ||
      ({
        gnis_name: river.name,
        levelpathid:
          river.levelpathids && river.levelpathids.length
            ? river.levelpathids[0]
            : null,
      } as ClickableStreamProps),
    // Gauged rivers paint the highlight in their overall verdict color
    // (the same palette as the discs); streams.ts falls back to red
    // when this is null.
    river.conditions ? river.conditions.overall : null,
  );
  // Re-clicking a shown gauge disc re-selects the same River object;
  // skip the disc rebuild so the element under the cursor (and its
  // hover tooltip) survives.
  if (changed && _gaugeRenderer) _gaugeRenderer.show(river);
}

/** Deselect. Hooked into closeRiverPanel(), the single choke point all
 *  close paths funnel through. */
export function clearRiverSelection(): void {
  _selectedRiver = null;
  if (_gaugeRenderer) _gaugeRenderer.hide();
}

/**
 * Re-resolve the selection against a freshly-loaded catalog. The
 * loaders replace River objects wholesale (state load, viewport load,
 * the z9 viewport-mode swap, SW stale-while-revalidate), so the held
 * reference must be re-matched -- by composite site_no, falling back to
 * name -- and the discs re-rendered to pick up fresh verdicts. When the
 * river isn't in the new list (e.g. panned away in viewport mode) the
 * old reference keeps the discs alive unchanged.
 */
export function refreshSelectedRiver(rivers: River[]): void {
  const cur = _selectedRiver;
  if (!cur) return;
  const next = rivers.find((r) =>
    cur.site_no ? r.site_no === cur.site_no : r.name === cur.name,
  );
  if (!next || next === cur) return;
  _selectedRiver = next;
  if (_gaugeRenderer) _gaugeRenderer.show(next);
  // Fresh data can change the overall verdict -- recolor the line
  // highlight to match the rebuilt discs (window: import-cycle dodge,
  // same as highlightStream above).
  window.setStreamHighlightVerdict(next.conditions ? next.conditions.overall : null);
}

// -- Window bridge ----------------------------------------------------

declare global {
  interface Window {
    // Consumed by closeRiverPanel (river-panel.ts) via window to avoid
    // an import cycle (this module imports openRiverPanel from there).
    clearRiverSelection: typeof clearRiverSelection;
  }
}

window.clearRiverSelection = clearRiverSelection;
