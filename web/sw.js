/* Service worker: offline-enable the ST web save editor.
 *
 * - Precaches the local app shell (HTML/CSS/JS + the desktop Python modules and
 *   the data files the editor fetches).
 * - Runtime-caches the Pyodide CDN assets (cache-first) so the big WASM runtime
 *   is fetched once and then works offline.
 *
 * Bump CACHE when any precached file changes to invalidate old caches.
 */
const CACHE = 'st-save-editor-v1';

const SHELL = [
  './',
  './index.html',
  './app.js',
  './style.css',
  './st_glue.py',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  '../st-editor/stsaveio.py',
  '../st-editor/stsaveedit.py',
  '../st-editor/stsave.py',
  '../st-editor/stsavefields.py',
  '../st-editor/data/st_ram_party_map.json',
  '../st-editor/data/st_shop_items.json',
  '../st-editor/data/st_runes.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const isPyodideCDN = url.hostname === 'cdn.jsdelivr.net' && url.pathname.includes('/pyodide/');

  // Cache-first for the app shell and the Pyodide runtime; fall back to network
  // and populate the cache so subsequent loads work offline. Opaque responses
  // (status 0) from the cross-origin <script> load are cached too — otherwise
  // pyodide.js itself would be missing offline.
  if (isPyodideCDN || url.origin === self.location.origin) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && (res.status === 200 || res.type === 'opaque')) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match('./index.html')))
    );
  }
});
