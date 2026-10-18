// StudyFlow service worker: installable app + an offline notice.
// Pages hold private task data and are sent with no-store, so they are never cached here;
// only the public design files and the offline page are.
const CACHE = 'studyflow-static-v9';
const PRECACHE = [
  '/static/notebook.css', '/static/dates.js', '/static/app.js', '/static/study.js', '/static/scan.js', '/static/app-v2.js', '/static/live.js', '/static/theme.js',
  '/static/brand-mark.svg', '/static/manifest.webmanifest', '/static/offline.html',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    // Serve the cached copy at once and refresh it in the background.
    event.respondWith(caches.open(CACHE).then(async cache => {
      const cached = await cache.match(request);
      const network = fetch(request).then(response => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      }).catch(() => cached);
      return cached || network;
    }));
  }
});

// Morning reminder sent by /cron/reminders. The text arrives encrypted for this device.
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || 'StudyFlow', {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: 'studyflow-morning',
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin);
  if (url.origin !== self.location.origin) return;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const open = windows.find(client => new URL(client.url).origin === self.location.origin);
    if (open) return open.navigate(url.href).then(client => (client || open).focus());
    return self.clients.openWindow(url.href);
  }));
});
