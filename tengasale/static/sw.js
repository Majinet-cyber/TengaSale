/**
 * TengaSale Service Worker — v1.0
 *
 * Caching strategy:
 *   Static assets (CSS/JS/images): cache-first
 *   HTML pages: network-first with offline fallback
 *   Payment / contract pages: never cached (privacy-sensitive)
 *   Admin / API / media: always skipped
 */

const CACHE_VERSION = "v1.0.3";
const STATIC_CACHE = `tengasale-static-${CACHE_VERSION}`;
const HTML_CACHE   = `tengasale-html-${CACHE_VERSION}`;
const OFFLINE_URL  = "/offline/";

// Static assets to precache at install
const PRECACHE_ASSETS = [
  "/static/css/style.css",
  "/static/css/website.css",
  "/static/css/tengasale.css",
  "/static/manifest.webmanifest",
  OFFLINE_URL,
];

// Paths that must NEVER be cached (private customer data)
const NEVER_CACHE_PATHS = [
  "/pay/contract/",
  "/pay/webhooks/",
  "/admin/",
  "/api/",
  "/media/",
];

function shouldNeverCache(url) {
  return NEVER_CACHE_PATHS.some((p) => url.pathname.startsWith(p));
}

// ── Install ────────────────────────────────────────────────────────────────

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll(PRECACHE_ASSETS).catch(() => {})
    )
  );
  self.skipWaiting();
});

// ── Activate ───────────────────────────────────────────────────────────────

self.addEventListener("activate", (event) => {
  const currentCaches = [STATIC_CACHE, HTML_CACHE];
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => !currentCaches.includes(key))
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch ──────────────────────────────────────────────────────────────────

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Never intercept sensitive paths — let them go straight to network
  if (shouldNeverCache(url)) return;

  // Cache-first for static assets
  if (
    url.pathname.startsWith("/static/") ||
    request.destination === "image" ||
    request.destination === "font" ||
    request.destination === "style" ||
    request.destination === "script"
  ) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first for HTML navigation (public pages only)
  if (request.mode === "navigate" || request.headers.get("accept")?.includes("text/html")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Only cache publicly-accessible pages (not /pay/contract/*)
          if (response.ok && !shouldNeverCache(url)) {
            const clone = response.clone();
            caches.open(HTML_CACHE).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() =>
          caches.match(request).then(
            (cached) =>
              cached ||
              caches.match(OFFLINE_URL) ||
              new Response("You are offline", { status: 503 })
          )
        )
    );
    return;
  }
});
