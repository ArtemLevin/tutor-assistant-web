const openDialog = (id, date) => {
  const dialog = document.getElementById(id);
  if (!dialog) return;
  if (date) {
    const start = dialog.querySelector('[name="starts_at"]');
    const end = dialog.querySelector('[name="ends_at"]');
    if (start && end) {
      start.value = `${date}T16:00`;
      end.value = `${date}T17:00`;
    }
  }
  dialog.showModal();
};

document.querySelectorAll('[data-auto-submit]').forEach((element) => {
  element.addEventListener('change', () => element.form?.requestSubmit());
});

document.querySelectorAll('[data-dialog-open]').forEach((button) => {
  button.addEventListener('click', () => openDialog(button.dataset.dialogOpen, button.dataset.date));
});
document.querySelectorAll('[data-dialog-close]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog')?.close());
});
document.querySelectorAll('dialog').forEach((dialog) => {
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
});

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    await navigator.clipboard.writeText(button.dataset.copy);
    const previous = button.textContent;
    button.textContent = 'Скопировано';
    setTimeout(() => { button.textContent = previous; }, 1400);
  });
});

document.querySelectorAll('[data-job-id]').forEach((element) => {
  const status = element.querySelector('[data-job-status]');
  if (!status || !['queued', 'running', 'retrying'].includes(status.textContent.trim())) return;
  const poll = async () => {
    try {
      const response = await fetch(`/api/jobs/${element.dataset.jobId}`);
      if (!response.ok) return;
      const job = await response.json();
      element.querySelector('[data-job-progress]').value = job.progress;
      element.querySelector('[data-job-message]').textContent = job.message;
      status.textContent = job.status;
      if (['queued', 'running', 'retrying'].includes(job.status)) setTimeout(poll, job.status === 'retrying' ? 5000 : 1800);
      else window.setTimeout(() => window.location.reload(), 600);
    } catch (_) { setTimeout(poll, 3000); }
  };
  setTimeout(poll, 800);
});

const canvas = document.getElementById('demo-board');
if (canvas) {
  const context = canvas.getContext('2d');
  let drawing = false;
  let eraser = false;
  const resize = () => {
    const image = context.getImageData(0, 0, canvas.width || 1, canvas.height || 1);
    canvas.width = canvas.clientWidth * devicePixelRatio;
    canvas.height = canvas.clientHeight * devicePixelRatio;
    context.putImageData(image, 0, 0);
    context.lineCap = 'round';
    context.lineJoin = 'round';
  };
  resize();
  window.addEventListener('resize', resize);
  const position = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [(event.clientX - rect.left) * devicePixelRatio, (event.clientY - rect.top) * devicePixelRatio];
  };
  canvas.addEventListener('pointerdown', (event) => {
    drawing = true; context.beginPath(); context.moveTo(...position(event)); canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!drawing) return;
    context.globalCompositeOperation = eraser ? 'destination-out' : 'source-over';
    context.strokeStyle = '#d7ff5f'; context.lineWidth = (eraser ? 22 : 3) * devicePixelRatio;
    context.lineTo(...position(event)); context.stroke();
  });
  canvas.addEventListener('pointerup', () => { drawing = false; });
  document.querySelectorAll('[data-tool]').forEach((button) => button.addEventListener('click', () => {
    eraser = button.dataset.tool === 'eraser';
    document.querySelectorAll('[data-tool]').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
  }));
  document.querySelector('[data-clear-board]')?.addEventListener('click', () => context.clearRect(0, 0, canvas.width, canvas.height));
}
