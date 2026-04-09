/**
 * renderer.js — Frontend logic for the Nikola Electron sidebar.
 *
 * Responsibilities:
 *  - Poll /rag/files every 60 s and render the file list
 *  - Handle file drag-and-drop / click-to-upload → POST /upload
 *  - Delete individual files via POST /rag/remove
 *  - Clear-all with "Yes, wipe" confirmation flow → POST /rag/clear-all
 *  - Chat: stream responses from POST /chat
 */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
const BACKEND = 'http://127.0.0.1:8000';
const POLL_INTERVAL_MS = 60_000;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dropZone       = document.getElementById('drop-zone');
const fileInput      = document.getElementById('file-input');
const uploadStatus   = document.getElementById('upload-status');
const fileList       = document.getElementById('file-list');
const fileCount      = document.getElementById('file-count');
const clearAllBtn    = document.getElementById('clear-all-btn');
const confirmBar     = document.getElementById('confirm-bar');
const btnYesWipe     = document.getElementById('btn-yes-wipe');
const btnCancelWipe  = document.getElementById('btn-cancel-wipe');
const chatMessages   = document.getElementById('chat-messages');
const chatInput      = document.getElementById('chat-input');
const chatSend       = document.getElementById('chat-send');
const statusDot      = document.getElementById('status-dot');
const statusText     = document.getElementById('status-text');
const refreshLabel   = document.getElementById('refresh-label');

// Session ID (per page-load)
const SESSION_ID = `session-${Date.now()}`;

// ── Status bar helpers ────────────────────────────────────────────────────────

function setStatus(ok, msg) {
  statusDot.className = ok ? 'ok' : 'err';
  statusText.textContent = msg;
}

// ── File list ─────────────────────────────────────────────────────────────────

async function fetchFiles() {
  try {
    const resp = await fetch(`${BACKEND}/rag/files`);
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    renderFiles(data.files || []);
    setStatus(true, `Backend connected · ${new Date().toLocaleTimeString()}`);
  } catch (err) {
    setStatus(false, `Backend unreachable: ${err.message}`);
  }
}

function renderFiles(files) {
  fileCount.textContent = files.length ? `(${files.length})` : '';
  fileList.innerHTML = '';

  if (files.length === 0) {
    fileList.innerHTML = '<p class="empty-hint">No documents indexed yet.</p>';
    return;
  }

  files.forEach(f => {
    const item = document.createElement('div');
    item.className = 'file-item';

    const nameEl = document.createElement('span');
    nameEl.className = 'file-name';
    nameEl.title = f.filename;
    nameEl.textContent = f.filename;

    const metaEl = document.createElement('span');
    metaEl.className = 'file-meta';
    metaEl.textContent = `${f.chunks} chunks`;

    const delBtn = document.createElement('button');
    delBtn.className = 'btn-delete';
    delBtn.title = `Remove ${f.filename}`;
    delBtn.textContent = '×';
    delBtn.addEventListener('click', () => deleteFile(f.filename, item));

    item.appendChild(nameEl);
    item.appendChild(metaEl);
    item.appendChild(delBtn);
    fileList.appendChild(item);
  });
}

async function deleteFile(filename, itemEl) {
  itemEl.style.opacity = '0.4';
  try {
    const resp = await fetch(`${BACKEND}/rag/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    await fetchFiles();
  } catch (err) {
    itemEl.style.opacity = '1';
    alert(`Failed to remove "${filename}":\n${err.message}`);
  }
}

// ── Clear-all flow ────────────────────────────────────────────────────────────

clearAllBtn.addEventListener('click', () => {
  confirmBar.style.display = 'flex';
  clearAllBtn.disabled = true;
});

btnCancelWipe.addEventListener('click', () => {
  confirmBar.style.display = 'none';
  clearAllBtn.disabled = false;
});

btnYesWipe.addEventListener('click', async () => {
  btnYesWipe.disabled = true;
  btnCancelWipe.disabled = true;
  try {
    const resp = await fetch(`${BACKEND}/rag/clear-all`, { method: 'POST' });
    if (!resp.ok) throw new Error(await resp.text());
    await fetchFiles();
  } catch (err) {
    alert(`Clear-all failed:\n${err.message}`);
  } finally {
    confirmBar.style.display = 'none';
    clearAllBtn.disabled = false;
    btnYesWipe.disabled = false;
    btnCancelWipe.disabled = false;
  }
});

// ── File upload ───────────────────────────────────────────────────────────────

async function uploadFile(file) {
  uploadStatus.textContent = `Uploading ${file.name}…`;
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch(`${BACKEND}/upload`, { method: 'POST', body: form });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg);
    }
    const data = await resp.json();
    uploadStatus.textContent = `✓ ${data.filename} — ${data.chunks} chunks indexed.`;
    await fetchFiles();
  } catch (err) {
    uploadStatus.textContent = `❌ Upload failed: ${err.message}`;
  }
}

// Click-to-select
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

// Drag-and-drop
dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

// ── Chat ──────────────────────────────────────────────────────────────────────

function appendMessage(role, text) {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.textContent = text;
  chatMessages.appendChild(el);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return el;
}

chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;

  chatInput.value = '';
  chatSend.disabled = true;
  appendMessage('user', text);

  const aiEl = appendMessage('ai', '…');

  try {
    const resp = await fetch(`${BACKEND}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: SESSION_ID }),
    });
    if (!resp.ok) throw new Error(resp.statusText);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let full = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      full += decoder.decode(value, { stream: true });
      aiEl.textContent = full;
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    if (!full.trim()) aiEl.textContent = '🤔 No response received.';
  } catch (err) {
    aiEl.textContent = `❌ ${err.message}`;
  } finally {
    chatSend.disabled = false;
    chatInput.focus();
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

let countdown = POLL_INTERVAL_MS / 1000;

setInterval(() => {
  countdown--;
  if (countdown <= 0) {
    countdown = POLL_INTERVAL_MS / 1000;
    fetchFiles();
  }
  refreshLabel.textContent = `refresh in ${countdown}s`;
}, 1000);

// ── Init ──────────────────────────────────────────────────────────────────────
fetchFiles();
