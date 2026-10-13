// StudyFlow 2.0: quick-add preview, keyboard shortcuts, long dates and the focus timer.
(() => {
  const lang = document.documentElement.lang === 'ru' ? 'ru-RU' : 'en-GB';
  const motionOk = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // "Monday, 13 October" in the Today header.
  document.querySelectorAll('time[data-format="long"]').forEach(element => {
    const parsed = new Date(element.getAttribute('datetime') + 'T12:00:00');
    if (Number.isNaN(parsed.getTime())) return;
    const text = new Intl.DateTimeFormat(lang, { weekday: 'long', day: 'numeric', month: 'long' }).format(parsed);
    element.textContent = text.charAt(0).toUpperCase() + text.slice(1);
  });

  // Quick add: the server parses the line, so the preview always matches what will be saved.
  const quick = document.querySelector('[data-quick-add]');
  const quickInput = quick?.querySelector('input[name="text"]');
  const preview = quick?.querySelector('[data-quick-preview]');
  const hint = preview?.textContent;
  let timer;
  let requestId = 0;
  const chip = (text, className, course) => {
    const element = document.createElement('span');
    element.className = 'chip ' + className;
    element.textContent = text;
    if (course) element.style.setProperty('--course', course);
    return element;
  };
  quickInput?.addEventListener('input', () => {
    clearTimeout(timer);
    const text = quickInput.value.trim();
    if (!text) { preview.textContent = hint; return; }
    timer = setTimeout(async () => {
      const current = ++requestId;
      try {
        const response = await fetch(quick.dataset.previewUrl + '?text=' + encodeURIComponent(text), { headers: { Accept: 'application/json' } });
        if (!response.ok || current !== requestId) return;
        const data = await response.json();
        const date = new Intl.DateTimeFormat(lang, { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(data.due_date + 'T12:00:00'));
        preview.replaceChildren(
          chip(date + (data.time ? ' · ' + data.time : ''), 'date'),
          chip(data.priority, data.priority_key.toLowerCase()),
          chip(data.course, 'course', data.course_color),
          chip(data.title || '…', 'title'),
        );
      } catch { /* Offline: keep the hint; the form still submits normally. */ }
    }, 220);
  });

  // Shortcuts: "/" jumps to quick add, "n" opens the full task form.
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
    const tag = event.target.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || event.target.isContentEditable) return;
    if (document.querySelector('.modal.show, dialog[open]')) return;
    if (event.key === '/' && quickInput) { event.preventDefault(); quickInput.focus(); }
    if (event.key === 'n') { const opener = document.querySelector('[data-open-modal]'); if (opener) { event.preventDefault(); opener.click(); } }
  });

  // Installable app with an offline notice.
  if ('serviceWorker' in navigator && location.protocol === 'https:' || location.hostname === 'localhost') {
    navigator.serviceWorker?.register('/sw.js').catch(() => {});
  }

  // One open task menu at a time; Escape or a tap outside closes it.
  const menus = document.querySelectorAll('.task-more');
  menus.forEach(menu => menu.addEventListener('toggle', () => {
    if (menu.open) menus.forEach(other => { if (other !== menu) other.open = false; });
  }));
  document.addEventListener('click', event => {
    menus.forEach(menu => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    menus.forEach(menu => { if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
  });

  // Focus timer.
  const board = document.querySelector('[data-focus]');
  if (!board || !board.querySelector('[data-focus-ring]')) return;
  const strings = JSON.parse(document.getElementById('focus-strings').textContent);
  const ring = board.querySelector('[data-focus-ring]');
  const timeLabel = board.querySelector('[data-focus-time]');
  const phaseLabel = board.querySelector('[data-focus-phase]');
  const toggle = board.querySelector('[data-focus-toggle]');
  const statsKey = 'studyflow-focus-' + new Date().toISOString().slice(0, 10);
  const readStats = () => { try { return JSON.parse(localStorage.getItem(statsKey)) || { sessions: 0, minutes: 0 }; } catch { return { sessions: 0, minutes: 0 }; } };
  const showStats = () => {
    const stats = readStats();
    board.querySelector('[data-focus-sessions]').textContent = stats.sessions;
    board.querySelector('[data-focus-minutes]').textContent = stats.minutes;
  };
  const addStats = minutes => {
    const stats = readStats();
    stats.sessions += 1; stats.minutes += minutes;
    try { localStorage.setItem(statsKey, JSON.stringify(stats)); } catch { /* Private mode: stats are not kept. */ }
    showStats();
  };

  let focusMinutes = 25, breakMinutes = 5, phase = 'focus', total = 25 * 60, left = total, endsAt = null, tick = null;
  const originalTitle = document.title;
  const render = () => {
    const minutes = Math.floor(left / 60), seconds = left % 60;
    const text = String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
    timeLabel.textContent = text;
    ring.style.setProperty('--ratio', String(1 - left / total));
    ring.classList.toggle('is-break', phase === 'break');
    ring.classList.toggle('is-running', Boolean(tick));
    phaseLabel.textContent = phase === 'focus' ? strings.focus : strings.break;
    toggle.textContent = tick ? strings.pause : (left < total ? strings.resume : strings.start);
    document.title = tick ? text + ' · ' + phaseLabel.textContent : originalTitle;
  };
  const notify = message => {
    if ('Notification' in window && Notification.permission === 'granted') new Notification('StudyFlow', { body: message });
    if (navigator.vibrate) navigator.vibrate([180, 80, 180]);
  };
  const setPhase = next => {
    phase = next;
    total = left = (next === 'focus' ? focusMinutes : breakMinutes) * 60;
  };
  const stop = () => { clearInterval(tick); tick = null; endsAt = null; };
  const step = () => {
    left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
    if (left === 0) {
      stop();
      if (phase === 'focus') { addStats(focusMinutes); notify(strings.ended); setPhase('break'); }
      else { notify(strings.back); setPhase('focus'); }
    }
    render();
  };
  toggle.addEventListener('click', () => {
    if (tick) { stop(); render(); return; }
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    endsAt = Date.now() + left * 1000;
    tick = setInterval(step, 250);
    render();
  });
  board.querySelector('[data-focus-reset]').addEventListener('click', () => { stop(); setPhase('focus'); render(); });
  board.querySelectorAll('[data-focus-mode]').forEach(button => button.addEventListener('click', () => {
    board.querySelectorAll('[data-focus-mode]').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
    focusMinutes = Number(button.dataset.minutes); breakMinutes = Number(button.dataset.break);
    stop(); setPhase('focus'); render();
  }));
  board.querySelector('[data-focus-select]')?.addEventListener('change', event => {
    if (tick && !window.confirm(strings.pause + '?')) return;
    event.target.form.requestSubmit();
  });
  window.addEventListener('beforeunload', event => { if (tick) { event.preventDefault(); event.returnValue = ''; } });
  if (!motionOk) ring.style.setProperty('transition', 'none');
  showStats();
  render();
})();
