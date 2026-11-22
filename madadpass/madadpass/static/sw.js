const cacheName = 'madadpass-shell-v2';
const shell = ['/', '/static/style.css', '/static/mark.svg', '/static/app.js', '/static/storage.js', '/static/voucher-qr.js'];
self.addEventListener('install', event => event.waitUntil(caches.open(cacheName).then(cache => cache.addAll(shell)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(
  keys.filter(key => key.startsWith('madadpass-shell-') && key !== cacheName).map(key => caches.delete(key)),
)).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || !shell.includes(url.pathname)) return;
  event.respondWith(fetch(request).then(response => {
    if (!response.ok) return caches.match(url.pathname).then(cached => cached || response);
    const duplicate = response.clone();
    caches.open(cacheName).then(cache => cache.put(url.pathname, duplicate)).catch(() => {});
    return response;
  }).catch(async () => (await caches.match(url.pathname)) || Response.error()));
});
