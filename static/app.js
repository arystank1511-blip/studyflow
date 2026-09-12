const modal = document.getElementById('task-modal');
const frame = document.querySelector('.app-frame');
const taskForm = modal?.querySelector('form');
let triggerButton;
let dirty = Boolean(modal?.dataset.reopen);
let submitting = false;

function openModal(event) {
  if (!modal) return;
  triggerButton = event?.currentTarget ?? document.querySelector('[data-open-modal]');
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
  button.textContent = 'Saving…';
});
window.addEventListener('beforeunload', event => {
  if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('pageshow', () => {
  submitting = false;
  const button = taskForm?.querySelector('button[type="submit"]');
  if (button) { button.disabled = false; button.textContent = 'Create task →'; }
});
document.querySelectorAll('time[datetime]').forEach(element => {
  const value = element.getAttribute('datetime');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return;
  const parsed = new Date(value + 'T12:00:00');
  if (!Number.isNaN(parsed.getTime())) element.textContent = new Intl.DateTimeFormat(undefined, {year:'numeric', month:'short', day:'numeric'}).format(parsed);
});
if (modal?.dataset.reopen) openModal();
