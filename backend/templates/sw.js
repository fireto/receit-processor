// Бележки service worker — caches versioned static files only, never pages or API responses.
// Static URLs carry ?v=<content hash>, so a new deploy means new URLs and a new cache;
// pages are always fetched from the network, so a reload shows the new version.
// Receipt photos (/receipts/<id>/photos/<seq>) are not static files: the browser
// revalidates them (a re-take replaces a photo under the same URL).
const CACHE = "receipts-static-{{ version }}";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin
      || !url.pathname.startsWith("/static/") || !url.searchParams.has("v")) {
    return; // the browser handles it: network, no SW caching
  }
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp.ok) cache.put(req, resp.clone());
        return resp;
      }))
    )
  );
});
