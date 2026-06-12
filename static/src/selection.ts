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

/**
 * Select a gauged river: open its detail panel and highlight its
 * reaches in the clickable network. `streamProps` carries the clicked
 * reach's identity when the selection came from a stream-line tap;
 * otherwise the river's own name / first levelpathid identify the
 * reaches to highlight.
 */
export function selectRiver(river: River, streamProps?: ClickableStreamProps): void {
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
  );
}

/** Deselect. Hooked into closeRiverPanel(), the single choke point all
 *  close paths funnel through. */
export function clearRiverSelection(): void {
  _selectedRiver = null;
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
