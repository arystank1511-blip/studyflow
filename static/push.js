// Morning reminders: subscribe this device to Web Push from the Progress page.
(() => {
  const box = document.querySelector('[data-push]');
  if (!box) return;
  const strings = JSON.parse(document.getElementById('push-strings').textContent);
  const button = box.querySelector('[data-push-toggle]');
  const status = box.querySelector('[data-push-status]');
  const csrf = box.querySelector('input[name="csrf_token"]').value;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  const say = (text, state) => { status.textContent = text; box.dataset.state = state || ''; };
  const keyBytes = base64 => {
    const padded = (base64 + '='.repeat((4 - base64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
  };
  const post = (url, fields) => fetch(url, { method: 'POST', credentials: 'same-origin', body: new URLSearchParams({ csrf_token: csrf, ...fields }) });

  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window) || location.protocol !== 'https:') {
    say(isIOS && !installed ? strings.ios : strings.unsupported, 'off');
    return;
  }

  let registration, subscription;
  const render = () => {
    button.hidden = false;
    button.disabled = false;
    if (subscription) { button.textContent = strings.turnOff; say(strings.on, 'on'); }
    else if (Notification.permission === 'denied') { button.hidden = true; say(strings.denied, 'off'); }
    else { button.textContent = strings.turnOn; say(strings.off, 'off'); }
  };

  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (subscription) {
        const endpoint = subscription.endpoint;
        await subscription.unsubscribe();
        subscription = null;
        await post('/push/unsubscribe', { endpoint });
      } else {
        if (await Notification.requestPermission() !== 'granted') { render(); return; }
        subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(box.dataset.key) });
        const keys = subscription.toJSON().keys || {};
        const response = await post('/push/subscribe', { endpoint: subscription.endpoint, p256dh: keys.p256dh || '', auth: keys.auth || '' });
        if (!response.ok) {
          const error = (await response.json().catch(() => ({}))).error;
          await subscription.unsubscribe();
          subscription = null;
          render();
          say(strings[error] || strings.failed, 'off');
          return;
        }
      }
      render();
    } catch {
      render();
      say(strings.failed, 'off');
    }
  });

  navigator.serviceWorker.register('/sw.js')
    .then(() => navigator.serviceWorker.ready)
    .then(async ready => { registration = ready; subscription = await ready.pushManager.getSubscription(); render(); })
    .catch(() => say(strings.unsupported, 'off'));
})();
