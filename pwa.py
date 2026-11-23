#!/usr/bin/env python3
"""Configures PWA static assets (`manifest.json`, `sw.js`, icons) and `.streamlit/config.toml`."""

import json
from pathlib import Path

ROOT = Path("/Users/gjvangreunen/workspace/takeazon_erp")
STREAMLIT_DIR = ROOT / ".streamlit"
STATIC_DIR = ROOT / "static"


def _create_png_icon(path: Path, size: int) -> None:
    """Creates a clean branded square PNG app icon without external dependencies."""
    import struct
    import zlib

    # Deep slate navy background (#0f172a) with emerald accent border (#10b981)
    bg = (15, 23, 42, 255)
    accent = (16, 185, 129, 255)
    white = (248, 250, 252, 255)

    margin = max(4, size // 12)
    border = max(3, size // 24)
    raw_rows = bytearray()

    for y in range(size):
        raw_rows.append(0)  # PNG filter type 0
        for x in range(size):
            in_outer = margin <= x < size - margin and margin <= y < size - margin
            in_inner = (margin + border) <= x < (size - margin - border) and (margin + border) <= y < (size - margin - border)
            # Draw bold "T" monogram in center
            cx = size // 2
            top_bar = (int(size * 0.30) <= y <= int(size * 0.40)) and (int(size * 0.28) <= x <= int(size * 0.72))
            stem = (int(size * 0.40) < y <= int(size * 0.72)) and (cx - int(size * 0.06) <= x <= cx + int(size * 0.06))

            if top_bar or stem:
                px = white
            elif in_outer and not in_inner:
                px = accent
            else:
                px = bg
            raw_rows.extend(px)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw_rows), 9)) + _chunk(b"IEND", b"")
    path.write_bytes(png_bytes)


def setup_pwa_files():
    STREAMLIT_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Enable static serving in .streamlit/config.toml (preserve existing settings if any)
    cfg_path = STREAMLIT_DIR / "config.toml"
    cfg_txt = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if "enableStaticServing" not in cfg_txt:
        if "[server]" in cfg_txt:
            cfg_txt = cfg_txt.replace("[server]", "[server]\nenableStaticServing = true", 1)
        else:
            cfg_txt = cfg_txt.rstrip() + "\n\n[server]\nenableStaticServing = true\n"
        cfg_path.write_text(cfg_txt.lstrip(), encoding="utf-8")
        print(f"Updated `{cfg_path}` with `enableStaticServing = true`.")

    # 2. Create 192x192 and 512x512 PNG icons
    _create_png_icon(STATIC_DIR / "icon-192.png", 192)
    _create_png_icon(STATIC_DIR / "icon-512.png", 512)
    print("Generated `static/icon-192.png` and `static/icon-512.png`.")

    # 3. Write Web App Manifest
    manifest = {
        "name": "Takeazon ERP — Operations & Marketplace Command",
        "short_name": "Takeazon ERP",
        "description": "Mobile-ready multi-channel pricing, Seller Flex sync, wave research, and warehouse operations.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {"src": "/app/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/app/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    (STATIC_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Created `static/manifest.json`.")

    # 4. Write Network-First Service Worker (keeps live ERP data fresh while enabling PWA install)
    sw_js = """const CACHE_NAME = 'takeazon-erp-v1';
const STATIC_ASSETS = [
  '/app/static/manifest.json',
  '/app/static/icon-192.png',
  '/app/static/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
"""
    (STATIC_DIR / "sw.js").write_text(sw_js, encoding="utf-8")
    print("Created `static/sw.js`.")


if __name__ == "__main__":
    setup_pwa_files()