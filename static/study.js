// YPT-style study stopwatch: one subject at a time, today's total keeps counting.
// The running session lives in localStorage so a reload or a closed tab does not lose it.
(() => {
  const root = document.querySelector('[data-study]');
  if (!root) return;
  const strings = JSON.parse(document.getElementById('study-strings').textContent);
  const csrf = document.querySelector('[data-study-csrf] input[name="csrf_token"]').value;
  const totalLabel = root.querySelector('[data-study-total]');
  const runningLabel = root.querySelector('[data-study-running]');
  const KEY = 'studyflow-study-running';
  const baseTitle = document.title;
  let timer = null;
  let wakeLock = null;

  const read = () => { try { return JSON.parse(localStorage.getItem(KEY)); } catch { return null; } };
  const write = value => { try { value ? localStorage.setItem(KEY, JSON.stringify(value)) : localStorage.removeItem(KEY); } catch { /* Not kept across reloads. */ } };
  const pad = value => String(value).padStart(2, '0');
  const format = (seconds, withSeconds) => {
    const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
    return withSeconds ? hours + ':' + pad(minutes) + ':' + pad(seconds % 60) : hours + ':' + pad(minutes);
  };
  const rowFor = course => [...document.querySelectorAll('[data-subject]')].find(row => row.dataset.subject === course);

  const keepAwake = async on => {
    try {
      if (on && 'wakeLock' in navigator && !wakeLock) wakeLock = await navigator.wakeLock.request('screen');
      if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
    } catch { wakeLock = null; }
  };

  const render = () => {
    const running = read();
    const elapsed = running ? Math.max(0, Math.floor((Date.now() - running.startedAt) / 1000)) : 0;
    totalLabel.textContent = format(Number(totalLabel.dataset.base) + elapsed, true);
    document.querySelectorAll('[data-subject]').forEach(row => {
      const active = running && row.dataset.subject === running.course;
      const label = row.querySelector('[data-subject-time]');
      label.textContent = format(Number(label.dataset.base) + (active ? elapsed : 0), Boolean(active));
      row.classList.toggle('is-running', Boolean(active));
      row.querySelector('[data-subject-toggle]').setAttribute('aria-pressed', String(Boolean(active)));
    });
    runningLabel.hidden = !running;
    if (running) runningLabel.textContent = strings.studying.replace('{course}', running.course);
    document.title = running ? format(elapsed, true) + ' · ' + running.course : baseTitle;
    root.classList.toggle('is-running', Boolean(running));
  };

  const start = course => {
    write({ course, startedAt: Date.now() });
    clearInterval(timer);
    timer = setInterval(render, 1000);
    keepAwake(true);
    render();
  };

  const stop = async () => {
    const running = read();
    if (!running) return;
    write(null);
    clearInterval(timer);
    keepAwake(false);
    const seconds = Math.floor((Date.now() - running.startedAt) / 1000);
    render();
    if (seconds < 60) { runningLabel.hidden = false; runningLabel.textContent = strings.short; return; }
    const body = new FormData();
    body.append('csrf_token', csrf);
    body.append('course', running.course);
    body.append('seconds', String(seconds));
    body.append('started_at', new Date(running.startedAt).toISOString());
    try {
      const response = await fetch(root.dataset.saveUrl, { method: 'POST', body, headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (!response.ok || !data.saved) throw new Error('not saved');
      const add = data.saved * 60;
      totalLabel.dataset.base = String(Number(totalLabel.dataset.base) + add);
      const row = rowFor(running.course);
      if (row) { const label = row.querySelector('[data-subject-time]'); label.dataset.base = String(Number(label.dataset.base) + add); }
      render();
    } catch {
      write(running);  // Keep the session so it can be saved on the next stop.
      runningLabel.hidden = false;
      runningLabel.textContent = strings.failed;
    }
  };

  document.querySelectorAll('[data-subject-toggle]').forEach(button => button.addEventListener('click', async () => {
    const course = button.closest('[data-subject]').dataset.subject;
    const running = read();
    if (running) await stop();
    if (!running || running.course !== course) start(course);
  }));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && read()) keepAwake(true); });

  if (read()) { timer = setInterval(render, 1000); keepAwake(true); }
  render();
})();
