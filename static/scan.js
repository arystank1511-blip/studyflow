// Timetable photo: preview it, shrink it to about 1600 px before upload (phone photos are
// often 5-10 MB), and show progress while the server reads it.
(() => {
  const form = document.querySelector('[data-scan-form]');
  if (!form) return;
  const input = form.querySelector('[data-scan-input]');
  const preview = form.querySelector('[data-scan-preview]');
  const label = form.querySelector('[data-scan-label]');
  const submit = form.querySelector('[data-scan-submit]');
  const strings = JSON.parse(document.getElementById('scan-strings').textContent);

  const shrink = file => new Promise(resolve => {
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, 1600 / Math.max(image.width, image.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(blob => resolve(blob ? new File([blob], 'timetable.jpg', { type: 'image/jpeg' }) : file), 'image/jpeg', 0.86);
      URL.revokeObjectURL(image.src);
    };
    image.onerror = () => resolve(file);
    image.src = URL.createObjectURL(file);
  });

  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    const smaller = await shrink(file);
    if (smaller !== file && window.DataTransfer) {
      const transfer = new DataTransfer();
      transfer.items.add(smaller);
      input.files = transfer.files;
    }
    preview.src = URL.createObjectURL(input.files[0]);
    preview.hidden = false;
    label.textContent = file.name;
  });

  form.addEventListener('submit', () => {
    submit.disabled = true;
    submit.textContent = strings.reading;
  });
})();
