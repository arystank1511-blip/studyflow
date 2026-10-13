const modal = document.getElementById('task-modal');
const ui = JSON.parse(document.getElementById('ui-strings').textContent);
const locale = ({ ru: 'ru-RU', kk: 'kk-KZ' })[document.documentElement.lang] || 'en-GB';
document.querySelectorAll('[data-auto-submit]').forEach(control => {
  control.addEventListener('change', () => control.form.requestSubmit());
});
const frame = document.querySelector('.app-frame');
const taskForm = modal?.querySelector('form');
let triggerButton;
let dirty = Boolean(modal?.dataset.reopen);
let submitting = false;

function openModal(event) {
  if (!modal) return;
  triggerButton = event?.currentTarget ?? document.querySelector('[data-open-modal]');
  const selectedDate = event?.currentTarget?.dataset.taskDate;
  if (selectedDate && /^\d{4}-\d{2}-\d{2}$/.test(selectedDate)) {
    taskForm.querySelector('[name="due_date"]').value = selectedDate;
  }
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
  frame.inert = true;
  document.body.classList.add('modal-open');
  (modal.querySelector('[aria-invalid="true"]') ?? modal.querySelector('input:not([type="hidden"])'))?.focus();
}

function closeModal() {
  if (!modal?.classList.contains('show')) return;
  modal.classList.remove('show');
  frame.inert = false;
  document.body.classList.remove('modal-open');
  triggerButton?.focus();
  modal.setAttribute('aria-hidden', 'true');
}

document.querySelectorAll('[data-open-modal]').forEach(button => button.addEventListener('click', openModal));
document.querySelectorAll('[data-close-modal]').forEach(button => button.addEventListener('click', closeModal));
modal?.addEventListener('click', event => { if (event.target === modal) closeModal(); });
document.addEventListener('keydown', event => {
  if (!modal?.classList.contains('show')) return;
  if (event.key === 'Escape') closeModal();
  if (event.key !== 'Tab') return;
  const controls = Array.from(modal.querySelectorAll('button:not([disabled]), input:not([type="hidden"]), select, textarea'));
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
taskForm?.addEventListener('input', () => { dirty = true; });
taskForm?.addEventListener('submit', () => {
  submitting = true;
  const button = taskForm.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = ui.saving;
});
window.addEventListener('beforeunload', event => {
  if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('pageshow', () => {
  submitting = false;
  const button = taskForm?.querySelector('button[type="submit"]');
  if (button) { button.disabled = false; button.textContent = ui.create + ' →'; }
});
document.querySelectorAll('time[datetime]').forEach(element => {
  const value = element.getAttribute('datetime');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
  const parsed = new Date(value + 'T12:00:00');
  if (!Number.isNaN(parsed.getTime())) element.textContent = new Intl.DateTimeFormat(locale, {year:'numeric', month:'short', day:'numeric'}).format(parsed);
});
if (modal?.dataset.reopen) openModal();

const editor = document.querySelector('[data-editor]');
editor?.addEventListener('input', () => { dirty = true; });
editor?.addEventListener('submit', () => {
  submitting = true;
  const button = editor.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = ui.saving;
});
editor?.querySelector('[aria-invalid="true"]')?.focus();
window.addEventListener('pageshow', () => {
  const button = editor?.querySelector('button[type="submit"]');
  if (button) { button.disabled = false; button.textContent = ui.save; }
});
const deleteDialog = document.getElementById('delete-dialog');
let pendingDelete;
let confirmedDelete;
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (confirmedDelete === form) { confirmedDelete = null; return; }
    event.preventDefault();
    if (!deleteDialog) return; // Fail closed if the confirmation UI is missing.
    pendingDelete = form;
    deleteDialog.showModal();
  });
});
deleteDialog?.querySelector('[data-cancel-delete]').addEventListener('click', () => deleteDialog.close());
deleteDialog?.addEventListener('close', () => { pendingDelete = null; });
deleteDialog?.querySelector('[data-confirm-delete]').addEventListener('click', () => {
  const form = pendingDelete;
  if (!form) return;
  confirmedDelete = form;
  deleteDialog.close();
  form.requestSubmit();
});

// Remember the browser timezone so the server knows which day is "today" for this student.
try {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zone && /^[A-Za-z0-9_+\-]+(\/[A-Za-z0-9_+\-]+)*$/.test(zone) && !document.cookie.split('; ').includes('studyflow_tz=' + zone)) {
    document.cookie = 'studyflow_tz=' + zone + '; path=/; max-age=31536000; samesite=lax' + (location.protocol === 'https:' ? '; secure' : '');
  }
} catch { /* Older browsers fall back to the server timezone. */ }

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Theme toggle: explicit choice wins over the system preference.
const themeToggle = document.querySelector('[data-theme-toggle]');
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
const isDark = () => (document.documentElement.dataset.theme ?? (darkQuery.matches ? 'dark' : 'light')) === 'dark';
const syncThemeButton = () => themeToggle?.setAttribute('aria-pressed', String(isDark()));
syncThemeButton();
darkQuery.addEventListener('change', syncThemeButton);
themeToggle?.addEventListener('click', () => {
  const next = isDark() ? 'light' : 'dark';
  const apply = () => {
    document.documentElement.dataset.theme = next;
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => { meta.content = next === 'dark' ? '#15171d' : '#f6f8fb'; });
    syncThemeButton();
  };
  try { localStorage.setItem('studyflow-theme', next); } catch { /* Choice lasts for this page only. */ }
  if (document.startViewTransition && !reduceMotion) quietTransition(document.startViewTransition(apply)); else apply();
});

// A skipped or timed-out transition is harmless; keep it out of the console.
function quietTransition(transition) {
  transition?.ready.catch(() => {});
  transition?.finished.catch(() => {});
}
window.addEventListener('pagereveal', event => quietTransition(event.viewTransition));
window.addEventListener('pageswap', event => quietTransition(event.viewTransition));

// Staggered entrance order for lists.
document.querySelectorAll('.work-task-list, .course-list, .week-grid').forEach(list => {
  Array.from(list.children).forEach((item, index) => item.style.setProperty('--i', index));
});

// Count percentages up from zero.
document.querySelectorAll('.completion-overview strong, .course-progress-head > strong').forEach(element => {
  const node = Array.from(element.childNodes).find(child => child.nodeType === Node.TEXT_NODE && /\d/.test(child.textContent));
  const target = parseInt(node?.textContent, 10);
  if (!node || Number.isNaN(target) || reduceMotion) return;
  const suffix = node.textContent.replace(/^\s*\d+/, '');
  const started = performance.now();
  const step = now => {
    const progress = Math.min(1, (now - started) / 1100);
    node.textContent = Math.round(target * (1 - Math.pow(1 - progress, 3))) + suffix;
    if (progress < 1) requestAnimationFrame(step);
  };
  node.textContent = '0' + suffix;
  requestAnimationFrame(step);
});

// One-click completion plays its check animation before the page reloads.
document.querySelectorAll('.quick-done-form').forEach(form => {
  form.addEventListener('submit', event => {
    const card = form.closest('.work-task');
    if (reduceMotion || !card || form.dataset.animated || card.classList.contains('is-done')) return;
    event.preventDefault();
    form.dataset.animated = 'true';
    card.classList.add('is-completing');
    form.querySelector('button').disabled = true;
    setTimeout(() => HTMLFormElement.prototype.submit.call(form), 520);
  });
});

// Success messages step aside after a few seconds; errors stay until the next action.
document.querySelectorAll('.flash.success').forEach(message => {
  setTimeout(() => {
    message.classList.add('is-leaving');
    message.addEventListener('animationend', () => message.remove(), { once: true });
  }, reduceMotion ? 6000 : 4200);
});
