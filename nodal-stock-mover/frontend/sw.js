/* NODAL service worker. App shell is cached for fast start and offline viewing of the last loaded data.
   Writes (approvals) are never cached or queued: they need a live connection. */
const VERSION = "nodal-v2";
const SHELL = ["/", "/static/styles.css", "/static/app.js", "/static/icons/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;                                   // never touch approvals / uploads
  if (url.pathname.startsWith("/api/")) {                             // data: network first, saved copy when offline (flagged as stale)
    e.respondWith(fetch(req).then((res) => {
      if (res.ok && !url.pathname.startsWith("/api/admin")) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(async () => {
      const hit = await caches.match(req);
      if (!hit) return new Response(JSON.stringify({ detail: "You are offline and this page was not saved." }), { status: 503, headers: { "Content-Type": "application/json" } });
      const h = new Headers(hit.headers); h.set("x-nodal-cache", "1");
      return new Response(await hit.blob(), { status: hit.status, headers: h });
    }));
    return;
  }
  if (req.mode === "navigate") {                                      // pages: network first, cached shell as fallback
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(req).then((hit) => {                      // assets, fonts, map libs: stale-while-revalidate
    const net = fetch(req).then((res) => { if (res.ok || res.type === "opaque") caches.open(VERSION).then((c) => c.put(req, res.clone())); return res; }).catch(() => hit);
    return hit || net;
  }));
});
