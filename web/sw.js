/* Service worker: offline-enable the ST web save editor + Web Share target.
 *
 * - App shell + desktop Python modules/data: NETWORK-FIRST (fresh on next online
 *   launch, cache fallback offline).
 * - Pyodide CDN (large, immutable, version-pinned): CACHE-FIRST (fetched once).
 * - Web Share target: catches the shared-in POST, stashes the file in a cache,
 *   and redirects to ?shared=1 so the app can pick it up on boot.
 *
 * Bump CACHE when any precached shell file changes to invalidate old caches.
 * Never purge SHARE_CACHE — it may hold a pending shared-in file.
 */
const CACHE = 'st-save-editor-v2';
const SHARE_CACHE = 'st-share';

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
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE && k !== SHARE_CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Web Share target: a file POSTed into the installed PWA.
  if (req.method === 'POST' && url.pathname.endsWith('/') && url.origin === self.location.origin) {
    e.respondWith((async () => {
      try {
        const form = await req.formData();
        const file = form.get('file');
        if (file) {
          const c = await caches.open(SHARE_CACHE);
          await c.put('shared-file', new Response(file, { headers: { 'x-filename': file.name || 'shared.save' } }));
        }
      } catch (err) { /* fall through to redirect */ }
      return Response.redirect('./?shared=1', 303);
    })());
    return;
  }

  if (req.method !== 'GET') return;
  const isPyodideCDN = url.hostname === 'cdn.jsdelivr.net' && url.pathname.includes('/pyodide/');
  const sameOrigin = url.origin === self.location.origin;

  if (isPyodideCDN) {
    // cache-first for the big immutable runtime
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && (res.status === 200 || res.type === 'opaque')) {
          const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
    return;
  }

  if (sameOrigin) {
    // network-first for the shell so deploys are picked up when online
    e.respondWith(
      fetch(req).then((res) => {
        if (res && res.status === 200) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => caches.match(req).then((hit) => hit || (req.mode === 'navigate' ? caches.match('./index.html') : undefined)))
    );
  }
});
