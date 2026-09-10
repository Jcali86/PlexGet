// Service worker: keeps the shell on the device, receives pushes, and opens
// the app when one is tapped.

// Bump this to force every device to take a fresh copy of the shell.
const SHELL = 'plexget-shell-v5';
// The manifest is deliberately NOT kept: it carries a one-time sign-in
// handoff for add-to-home-screen, and a cached copy would bake a dead code
// into every install.
const KEEP = ['/request',
              '/icons/apple-touch-icon.png', '/icons/icon-192.png'];

// Milliseconds the server gets before the held shell paints instead. A launch
// carrying a handoff code is given longer, because painting early forfeits
// the sign-in it carries until the next launch.
const PATIENCE = 1200;
const HANDOFF_PATIENCE = 3000;

self.addEventListener('install', (e) => {
  // A page held on the device paints the moment it is asked for, so a slow
  // connection shows the app rather than white while the network catches up.
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(KEEP)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // A worker that installed while the server was away holds nothing: the
    // install fetch failed, quietly, and throwing the previous version's copy
    // out now would leave the next launch with no shell at all - on a phone,
    // a white screen for as long as the network is prepared to wait. The old
    // copy is carried forward instead, and the next visit that reaches the
    // server replaces it.
    const shell = await caches.open(SHELL);
    if (!(await shell.match('/request'))) {
      for (const name of await caches.keys()) {
        if (name === SHELL) continue;
        const kept = await (await caches.open(name)).match('/request');
        if (kept) { await shell.put('/request', kept); break; }
      }
    }
    for (const name of await caches.keys()) {
      if (name !== SHELL) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Anything that answers about a person - who they are, what they asked for,
  // what is on the shelf - must come from the server every time. Only the
  // shell and its artwork are held.
  // Only the household page is the shell. Treating every navigation as the
  // shell meant any other address on this host - the operations dashboard, a
  // test page, anything - was answered with a cached copy of PlexGet.
  const isShell = url.pathname === '/' || url.pathname === '/request';
  const isAsset = url.pathname.startsWith('/icons/');
  if (!isShell && !isAsset) return;
  // A launch carrying a handoff code is trading the code for a session, so
  // the server has to be asked - a held shell handed straight back would
  // swallow the trade. It used to be left to the network entirely, and an
  // installed app opens on this address every single time, so whenever the
  // server was asleep the app sat on a white screen for as long as the phone
  // was prepared to wait. Now it is asked, given longer than usual, and then
  // the held shell paints anyway: by the second launch the code has been
  // spent and the server's answer is a redirect home, which is what the held
  // copy already is.
  const handoff = isShell && url.searchParams.has('handoff');

  event.respondWith((async () => {
    const cache = await caches.open(SHELL);
    const key = isShell ? '/request' : req;

    // Artwork and the manifest never change under the same name, so the held
    // copy is always right.
    if (isAsset) {
      const hit = await cache.match(key);
      if (hit) return hit;
    }

    const fresh = fetch(req).then((res) => {
      // A handoff is answered with a redirect, never with the shell, so
      // there is nothing in it worth keeping.
      if (res && res.ok && !handoff) cache.put(key, res.clone());
      return res;
    }).catch(() => null);

    // Race the server against the patience of somebody holding a phone. The
    // server usually wins, so the app is never a version behind; when it does
    // not, the held copy paints at once and the fresh one lands in the cache
    // for next time. Waiting on the network alone is what makes an app look
    // broken on a bad signal.
    const hit = await cache.match(key);
    if (!hit) return (await fresh) || Response.error();

    const winner = await Promise.race([
      fresh,
      new Promise((resolve) => setTimeout(() => resolve(null),
                                          handoff ? HANDOFF_PATIENCE : PATIENCE)),
    ]);
    return winner || hit;
  })());
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const title = data.title || 'PlexGet';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag || title,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of all) {
      if ('focus' in client) { await client.focus(); if (client.navigate) await client.navigate(url); return; }
    }
    if (clients.openWindow) await clients.openWindow(url);
  })());
});
