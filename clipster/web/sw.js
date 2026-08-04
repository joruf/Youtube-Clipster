/*
 * Service worker of the phone interface.
 *
 * It exists so Android lets the page be installed to the home screen - which is
 * what puts Clipster into the share sheet. It deliberately does almost nothing
 * else: caching API answers would show yesterday's download list, and caching
 * media would fill the phone with copies of files that live on the PC.
 *
 * Only the shell is kept, and only as a fallback for when the PC is unreachable.
 */

"use strict";

const SHELL_CACHE = "clipster-shell-v1";
const SHELL = ["/", "/index.html", "/style.css", "/app.js", "/icon.png"];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            .then((cache) => cache.addAll(SHELL))
            .catch(() => undefined)
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(
                names.filter((name) => name !== SHELL_CACHE).map((name) => caches.delete(name))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    const url = new URL(request.url);
    const live = request.method !== "GET"
        || url.pathname.startsWith("/api/")
        || url.pathname.startsWith("/media/");
    if (live) {
        // Never answer these from a cache - and never store them either.
        return;
    }
    // Network first: an updated interface has to reach the phone, so the cache
    // is only what is left when the PC cannot be reached.
    event.respondWith(
        fetch(request)
            .then((response) => {
                const copy = response.clone();
                caches.open(SHELL_CACHE)
                    .then((cache) => cache.put(request, copy))
                    .catch(() => undefined);
                return response;
            })
            .catch(() => caches.match(request).then((hit) => hit || Response.error()))
    );
});
