"""
Where the bundled data files come from.

Today the NHDPlus VAA + clickable-stream files (~9 MB) are committed to
the repo and baked into the image -- `resolve_data_file` just returns the
local path. At national scale those files grow to ~50-150 MB, too big to
ship in the image; set `DATA_BASE_URL` to host them externally (Cloudflare
R2 or a GitHub release) and they're downloaded once at startup instead.
This seam means that switch is config, not code.
"""

import logging
import os

import httpx

logger = logging.getLogger("blueliner.data")

# Unset by default -> use the bundled local files (current behavior).
DATA_BASE_URL = os.environ.get("DATA_BASE_URL", "").strip().rstrip("/")
_CACHE_DIR = os.environ.get("DATA_CACHE_DIR", "/tmp/blueliner-data")


def resolve_data_file(local_path: str, filename: str) -> str:
    """Return a usable path for `filename`:
      1. the bundled `local_path` if it exists (dev + today's small files);
      2. else, if DATA_BASE_URL is set, download `{DATA_BASE_URL}/{filename}`
         to a cache dir once and return that;
      3. else `local_path` unchanged (the loader then no-ops, as today).
    """
    if os.path.exists(local_path):
        return local_path
    if not DATA_BASE_URL:
        return local_path

    os.makedirs(_CACHE_DIR, exist_ok=True)
    dest = os.path.join(_CACHE_DIR, filename)
    if os.path.exists(dest):
        return dest

    url = f"{DATA_BASE_URL}/{filename}"
    tmp = dest + ".part"
    try:
        with httpx.stream("GET", url, timeout=180.0,
                          follow_redirects=True) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        os.replace(tmp, dest)              # atomic: no half-file on crash
        logger.info("downloaded %s (%.1f MB)", filename,
                    os.path.getsize(dest) / 1e6)
        return dest
    except Exception as exc:
        logger.warning("data download failed for %s: %s", url, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return local_path
