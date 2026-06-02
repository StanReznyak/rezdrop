const form = document.getElementById('uploadForm');
const input = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const fileList = document.getElementById('fileList');
const progressWrap = document.getElementById('progressWrap');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatSize(bytes) {
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  let value = Number(bytes || 0);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function renderFiles() {
  const files = Array.from(input.files || []);
  if (!files.length) {
    fileList.className = 'file-list empty';
    fileList.textContent = 'Файлы пока не выбраны';
    return;
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  fileList.className = 'file-list';
  fileList.innerHTML = `
    <div class="file-list-head"><b>${files.length} файлов</b><span>${formatSize(total)}</span></div>
    ${files.map(file => `<div class="file-row"><span>${escapeHtml(file.name)}</span><em>${formatSize(file.size)}</em></div>`).join('')}
  `;
}

['dragenter', 'dragover'].forEach(eventName => {
  dropzone.addEventListener(eventName, event => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  });
});

['dragleave', 'drop'].forEach(eventName => {
  dropzone.addEventListener(eventName, event => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
  });
});

dropzone.addEventListener('drop', event => {
  input.files = event.dataTransfer.files;
  renderFiles();
});

input.addEventListener('change', renderFiles);

form.addEventListener('submit', event => {
  event.preventDefault();
  const data = new FormData(form);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', form.action);

  progressWrap.hidden = false;
  progressBar.style.width = '0%';
  progressText.textContent = 'Загрузка...';

  xhr.upload.addEventListener('progress', event => {
    if (!event.lengthComputable) return;
    const percent = Math.round((event.loaded / event.total) * 100);
    progressBar.style.width = `${percent}%`;
    progressText.textContent = `Загрузка: ${percent}%`;
  });

  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 400) {
      progressBar.style.width = '100%';
      progressText.textContent = 'Готово. Открываю ссылку...';
      window.location.href = xhr.responseURL || '/';
      return;
    }

    let message = 'Ошибка загрузки';
    try {
      const data = JSON.parse(xhr.responseText);
      message = data.detail || message;
    } catch (_) {
      if (xhr.responseText) message = xhr.responseText.slice(0, 300);
    }
    progressText.textContent = message;
  };

  xhr.onerror = () => {
    progressText.textContent = 'Ошибка сети при загрузке';
  };

  xhr.send(data);
});
