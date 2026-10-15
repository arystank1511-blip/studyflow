// Task actions without a full reload. The form posts exactly as before; the page the server
// redirects to is fetched in the background and only the regions marked data-live are swapped.
(() => {
  window.StudyFlow = window.StudyFlow || { enhancers: [] };
  const actions = /^\/tasks\/(\d+\/(status|snooze|undo|delete)|reschedule-overdue)$/;
  let queue = Promise.resolve();

  const handles = form => form.method.toLowerCase() === 'post'
    && actions.test(new URL(form.action, location.href).pathname)
    && document.querySelector('[data-live]');

  // A page we cannot patch (an error, another page, a lost session) is shown the ordinary way.
  const showWhole = (response, html) => {
    if (response.ok && response.redirected) { location.assign(response.url); return; }
    const page = new DOMParser().parseFromString(html, 'text/html');
    document.title = page.title;
    document.body.replaceWith(document.adoptNode(page.body));
  };

  const swap = page => {
    const focusedRow = document.activeElement?.closest('.work-task');
    const focusKey = focusedRow?.querySelector('form')?.getAttribute('action');
    document.documentElement.classList.remove('fresh-day'); // Entrance animations play once per day only.
    document.querySelectorAll('[data-live]').forEach(region => {
      const next = page.querySelector(`[data-live="${region.dataset.live}"]`);
      if (!next) return;
      const fresh = document.adoptNode(next);
      region.replaceWith(fresh);
      window.StudyFlow.enhancers.forEach(setup => setup(fresh));
    });
    // Keep keyboard users in place: the same task if it is still listed, otherwise the list.
    if (focusKey) {
      const target = Array.from(document.querySelectorAll('.work-task form')).find(form => form.getAttribute('action') === focusKey);
      (target?.querySelector('button') || document.getElementById('task-heading'))?.focus({ preventScroll: true });
    }
  };

  const run = async form => {
    const card = form.closest('.work-task');
    const body = new FormData(form);
    card?.classList.add('is-pending');
    let response;
    try {
      response = await fetch(form.action, { method: 'POST', body, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
    } catch {
      // Nothing reached the server, so a normal submit is safe (offline shows the offline page).
      card?.classList.remove('is-pending');
      HTMLFormElement.prototype.submit.call(form);
      return;
    }
    const html = await response.text();
    const samePage = response.ok && response.redirected && new URL(response.url).pathname === location.pathname;
    const page = samePage && new DOMParser().parseFromString(html, 'text/html');
    if (!page || !page.querySelector('[data-live]')) { showWhole(response, html); return; }
    const apply = () => swap(page);
    if (document.startViewTransition && !document.hidden && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const transition = document.startViewTransition(apply);
      transition.ready.catch(() => {});
      transition.finished.catch(() => {});
      await transition.updateCallbackDone.catch(() => {});
    } else {
      apply();
    }
  };

  // One action at a time, in the order they were made.
  const send = form => { queue = queue.then(() => run(form)).catch(() => location.reload()); return queue; };
  window.StudyFlow.send = send;

  // Runs after app.js, so a delete waiting for confirmation or a check mark mid-animation is left alone.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement) || !handles(form)) return;
    event.preventDefault();
    send(form);
  });
})();
