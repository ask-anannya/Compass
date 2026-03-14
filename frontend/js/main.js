// ── Elements ──────────────────────────────────────────────────────────────────
const urlInput         = document.getElementById('urlInput');
const analyseBtn       = document.getElementById('analyseBtn');
const progressWrap     = document.getElementById('progressWrap');
const progressLog      = document.getElementById('progressLog');
const outputPanel      = document.getElementById('outputPanel');
const audioBtn         = document.getElementById('audioBtn');
const pdfBtn           = document.getElementById('pdfBtn');
const audioWrap        = document.getElementById('audioWrap');
const audioStatus      = document.getElementById('audioStatus');
const transcriptEl     = document.getElementById('transcript');
const textBriefStatus  = document.getElementById('textBriefStatus');
const textBriefContent = document.getElementById('textBriefContent');
const heroSection      = document.getElementById('heroSection');
const sidebar          = document.getElementById('sidebar');
const sidebarToggle    = document.getElementById('sidebarToggle');
const topbarToggle     = document.getElementById('topbarToggle');
const newAnalysisBtn   = document.getElementById('newAnalysisBtn');
const recentList       = document.getElementById('recentList');
const chatArea         = document.getElementById('chatArea');
const chatMessages     = document.getElementById('chatMessages');
const content          = document.getElementById('content');
const navProgressBar   = document.getElementById('navProgressBar');

// ── App state ─────────────────────────────────────────────────────────────────
let currentSessionId = null;
let audioManager     = null;
let audioActive      = false;
let appMode          = 'url'; // 'url' | 'chat'

// ── Session store (localStorage) ─────────────────────────────────────────────
const SESSIONS_KEY = 'compass_sessions';

function getSessions() {
  return JSON.parse(localStorage.getItem(SESSIONS_KEY) || '[]');
}

function saveSessions(arr) {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(arr.slice(0, 20)));
}

function upsertSession(patch) {
  const sessions = getSessions();
  const idx = sessions.findIndex(s => s.sessionId === patch.sessionId);
  if (idx >= 0) {
    sessions[idx] = { ...sessions[idx], ...patch };
  } else {
    sessions.unshift(patch);
  }
  saveSessions(sessions);
}

function deleteSession(sessionId) {
  saveSessions(getSessions().filter(s => s.sessionId !== sessionId));
  if (sessionId === currentSessionId) resetToUrlMode();
  renderSidebar();
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function setSidebarCollapsed(collapsed) {
  sidebar.classList.toggle('collapsed', collapsed);
  topbarToggle.classList.toggle('visible', collapsed);
}

sidebarToggle.addEventListener('click', () => setSidebarCollapsed(true));
topbarToggle.addEventListener('click', () => {
  if (window.innerWidth <= 768) {
    sidebar.classList.toggle('mobile-open');
  } else {
    setSidebarCollapsed(false);
  }
});

function renderSidebar() {
  recentList.innerHTML = '';
  for (const s of getSessions()) {
    const item = document.createElement('div');
    item.className = 'session-item' + (s.sessionId === currentSessionId ? ' active' : '');

    const name = document.createElement('span');
    name.className   = 'session-name';
    name.textContent = s.repoName || s.sessionId.slice(0, 8);

    const del = document.createElement('button');
    del.className   = 'session-delete';
    del.textContent = '🗑';
    del.title       = 'Delete session';
    del.addEventListener('click', e => { e.stopPropagation(); deleteSession(s.sessionId); });

    item.appendChild(name);
    item.appendChild(del);
    item.addEventListener('click', () => loadSession(s.sessionId));
    recentList.appendChild(item);
  }
}

renderSidebar();

// ── Load a saved session ──────────────────────────────────────────────────────
function loadSession(sessionId) {
  const session = getSessions().find(s => s.sessionId === sessionId);
  if (!session) return;

  stopAudio();
  currentSessionId = sessionId;

  // Reset all panels
  progressLog.innerHTML       = '';
  progressWrap.style.display  = 'none';
  textBriefContent.innerHTML  = '';
  textBriefStatus.textContent = '';
  transcriptEl.textContent    = '';
  chatMessages.innerHTML      = '';
  chatArea.style.display      = 'none';
  audioWrap.style.display     = 'none';
  heroSection.style.display   = 'none';

  outputPanel.style.display = 'flex';
  audioBtn.style.display    = '';
  pdfBtn.style.display      = '';
  audioBtn.disabled = false;
  pdfBtn.disabled   = false;

  if (session.textBrief) renderMarkdown(session.textBrief, textBriefContent);

  // Replay chat history
  for (const turn of (session.chatHistory || [])) {
    appendMessage(turn.role === 'user' ? 'user' : 'ai', turn.text);
  }

  activateChatMode();
  renderSidebar();
  if (window.innerWidth <= 768) sidebar.classList.remove('mobile-open');
  setTimeout(() => { content.scrollTop = content.scrollHeight; }, 50);
}

// ── Reset to URL mode ─────────────────────────────────────────────────────────
function resetToUrlMode() {
  stopAudio();
  appMode          = 'url';
  currentSessionId = null;

  heroSection.style.display   = 'flex';
  outputPanel.style.display   = 'none';
  progressWrap.style.display  = 'none';
  progressLog.innerHTML       = '';
  textBriefContent.innerHTML  = '';
  textBriefStatus.textContent = '';
  transcriptEl.textContent    = '';
  chatMessages.innerHTML      = '';
  chatArea.style.display      = 'none';
  audioWrap.style.display     = 'none';

  audioBtn.style.display = 'none';
  pdfBtn.style.display   = 'none';
  audioBtn.disabled = true;
  pdfBtn.disabled   = true;

  urlInput.placeholder = 'Paste a GitHub URL to get started';
  urlInput.value       = '';
  analyseBtn.disabled  = false;
  urlInput.focus();
}

newAnalysisBtn.addEventListener('click', () => {
  resetToUrlMode();
  renderSidebar();
  if (window.innerWidth <= 768) sidebar.classList.remove('mobile-open');
});

// ── Ingestion ─────────────────────────────────────────────────────────────────
analyseBtn.addEventListener('click', () => {
  if (appMode === 'chat') sendChatMessage();
  else                    startIngestion();
});

urlInput.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (appMode === 'chat') sendChatMessage();
  else                    startIngestion();
});

async function startIngestion() {
  const url = urlInput.value.trim();
  if (!url) return;

  analyseBtn.disabled         = true;
  progressLog.innerHTML       = '';
  progressWrap.style.display  = 'block';
  outputPanel.style.display   = 'none';
  textBriefContent.innerHTML  = '';
  textBriefStatus.innerHTML   = '';
  transcriptEl.textContent    = '';
  currentSessionId            = null;
  heroSection.style.display   = 'none';

  // Nav progress bar
  navProgressBar.style.width = '0%';
  navProgressBar.classList.add('active');

  try {
    const resp = await fetch('/ingest', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ github_url: url }),
    });

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const line of decoder.decode(value).split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        appendProgress(ev.event, ev.msg || ev.session_id || '');
        if (ev.event === 'start')    currentSessionId = ev.session_id;
        if (ev.event === 'complete') onIngestionComplete(ev.session_id, url);
        if (ev.event === 'error')    analyseBtn.disabled = false;
      }
    }
  } catch (err) {
    appendProgress('error', err.message);
    analyseBtn.disabled = false;
  }
}

// ── Progress log ──────────────────────────────────────────────────────────────
const PASS_EVENTS = new Set(['pass_1', 'pass_2', 'pass_3', 'pass_4']);
let _lastPassType = null;
let _tickerEl     = null;

const NAV_PROGRESS = {
  cloning: '10%', reading: '20%',
  pass_1:  '35%', pass_2:  '55%', pass_3: '72%', pass_4: '90%',
};

function appendProgress(event, msg) {
  // Nav progress bar
  if (NAV_PROGRESS[event]) navProgressBar.style.width = NAV_PROGRESS[event];
  if (event === 'complete') {
    navProgressBar.style.width = '100%';
    setTimeout(() => {
      navProgressBar.classList.remove('active');
      navProgressBar.style.width = '0%';
    }, 600);
  }
  if (event === 'error') {
    navProgressBar.classList.remove('active');
    navProgressBar.style.width = '0%';
  }

  const isPass = PASS_EVENTS.has(event);

  if (isPass && event === _lastPassType && _tickerEl) {
    _tickerEl.textContent = `  → ${msg}`;
    progressLog.scrollTop = progressLog.scrollHeight;
    return;
  }

  _lastPassType = isPass ? event : null;
  _tickerEl     = null;

  const line = document.createElement('div');
  line.className = `ev-${event}`;

  if (event === 'start') {
    line.innerHTML =
      `<span style="color:#00D4AA">[start]</span>` +
      `<span style="font-family:monospace;font-size:0.72rem;color:#484F58;margin-left:6px">${msg}</span>`;
  } else {
    line.textContent = `[${event}] ${msg}`;
  }

  progressLog.appendChild(line);

  if (isPass) {
    _tickerEl = document.createElement('div');
    _tickerEl.className = `ev-${event}`;
    _tickerEl.style.cssText = 'color:#00D4AA;opacity:0.6;padding-left:14px;font-size:0.75rem;';
    progressLog.appendChild(_tickerEl);
  }

  progressLog.scrollTop = progressLog.scrollHeight;
}

// ── Post-ingestion ────────────────────────────────────────────────────────────
async function onIngestionComplete(sessionId, repoUrl) {
  currentSessionId          = sessionId;
  outputPanel.style.display = 'flex';
  audioBtn.style.display    = '';
  pdfBtn.style.display      = '';
  audioBtn.disabled         = false;
  pdfBtn.disabled           = false;

  const match    = (repoUrl || '').match(/github\.com\/[^/]+\/([^/]+)/);
  const repoName = match ? match[1].replace(/\.git$/, '') : sessionId.slice(0, 8);

  textBriefStatus.innerHTML = '<span class="spinner"></span>Generating text brief...';

  let textBrief = '';
  try {
    const resp = await fetch(`/brief/text/${sessionId}`);
    const data = await resp.json();
    textBriefStatus.textContent = '';
    textBrief = data.brief;
    renderMarkdown(textBrief, textBriefContent);
  } catch (err) {
    textBriefStatus.textContent = `Failed to load text brief: ${err.message}`;
  }

  upsertSession({ sessionId, repoName, repoUrl, createdAt: Date.now(), textBrief, chatHistory: [] });
  renderSidebar();
  activateChatMode();
  analyseBtn.disabled = false;
}

// ── Chat mode ─────────────────────────────────────────────────────────────────
function activateChatMode() {
  appMode                  = 'chat';
  urlInput.placeholder     = 'Ask anything about this codebase...';
  chatArea.style.display   = 'flex';
  if (!chatMessages.children.length) {
    appendMessage('ai', 'Ready — ask me anything about this codebase.');
  }
  urlInput.focus();
}

async function sendChatMessage() {
  const text = urlInput.value.trim();
  if (!text || !currentSessionId) return;

  urlInput.value      = '';
  analyseBtn.disabled = true;

  appendMessage('user', text);
  const aiBubble = appendMessage('ai', '', true); // streaming=true
  const textEl   = aiBubble.querySelector('.msg-text');

  let fullResponse = '';
  try {
    const resp = await fetch(`/chat/${currentSessionId}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text }),
    });

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.event === 'chunk') {
          fullResponse += ev.text;
          textEl.innerHTML = markdownToHtml(fullResponse);
          content.scrollTop = content.scrollHeight;
        }
      }
    }
  } catch (err) {
    textEl.textContent = `Error: ${err.message}`;
  }

  aiBubble.classList.remove('streaming');
  analyseBtn.disabled = false;
  urlInput.focus();

  // Persist turns to localStorage
  const sessions = getSessions();
  const session  = sessions.find(s => s.sessionId === currentSessionId);
  if (session) {
    session.chatHistory = session.chatHistory || [];
    session.chatHistory.push({ role: 'user',  text });
    session.chatHistory.push({ role: 'model', text: fullResponse });
    if (session.chatHistory.length > 40) session.chatHistory = session.chatHistory.slice(-40);
    saveSessions(sessions);
  }
}

function appendMessage(role, text, streaming = false) {
  const row    = document.createElement('div');
  row.className = `msg-row msg-row-${role}`;

  const bubble = document.createElement('div');
  bubble.className = `msg-bubble msg-${role}${streaming ? ' streaming' : ''}`;

  const textEl = document.createElement('div');
  textEl.className = 'msg-text';
  if (text) {
    textEl.innerHTML = role === 'ai' ? markdownToHtml(text) : escapeHtml(text);
  }

  bubble.appendChild(textEl);
  row.appendChild(bubble);
  chatMessages.appendChild(row);
  content.scrollTop = content.scrollHeight;
  return bubble;
}

// ── Markdown rendering ────────────────────────────────────────────────────────
function escapeHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function inlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g,        '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,  '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g,      '<em>$1</em>')
    .replace(/_([^_]+)_/g,        '<em>$1</em>');
}

function markdownToHtml(md) {
  const lines = md.split('\n');
  let html = '';
  let inUl = false, inOl = false;

  const closeLists = () => {
    if (inUl) { html += '</ul>'; inUl = false; }
    if (inOl) { html += '</ol>'; inOl = false; }
  };

  for (const line of lines) {
    if (line.startsWith('### ')) {
      closeLists();
      html += `<h3>${inlineMarkdown(line.slice(4))}</h3>`;
    } else if (line.startsWith('## ')) {
      closeLists();
      html += `<h2>${inlineMarkdown(line.slice(3))}</h2>`;
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      if (inOl) { html += '</ol>'; inOl = false; }
      if (!inUl) { html += '<ul>'; inUl = true; }
      html += `<li>${inlineMarkdown(line.slice(2))}</li>`;
    } else if (/^\d+\. /.test(line)) {
      if (inUl) { html += '</ul>'; inUl = false; }
      if (!inOl) { html += '<ol>'; inOl = true; }
      html += `<li>${inlineMarkdown(line.replace(/^\d+\. /, ''))}</li>`;
    } else if (line.trim()) {
      closeLists();
      html += `<p>${inlineMarkdown(line)}</p>`;
    } else {
      closeLists();
    }
  }
  closeLists();
  return html;
}

function setInline(el, text) { el.innerHTML = inlineMarkdown(text); }

function renderMarkdown(md, el) {
  el.innerHTML = markdownToHtml(md);
}

// ── Audio brief ───────────────────────────────────────────────────────────────
audioBtn.addEventListener('click', () => {
  if (audioActive) stopAudio();
  else             startAudio();
});

function startAudio() {
  if (!currentSessionId) return;
  audioActive              = true;
  audioBtn.textContent     = '⏹ Stop Audio Brief';
  audioBtn.classList.add('active');
  audioWrap.style.display  = 'block';
  transcriptEl.textContent = '';
  audioStatus.textContent  = 'Connecting...';

  audioManager = new AudioManager(
    currentSessionId,
    text   => { transcriptEl.textContent += text + ' '; transcriptEl.scrollTop = transcriptEl.scrollHeight; },
    status => { audioStatus.textContent = status; }
  );
  audioManager.start();
}

function stopAudio() {
  if (!audioActive) return;
  audioActive = false;
  audioBtn.textContent = '🎙 Audio Brief';
  audioBtn.classList.remove('active');
  if (audioManager) { audioManager.stop(); audioManager = null; }
}

// ── PDF download ──────────────────────────────────────────────────────────────
pdfBtn.addEventListener('click', () => {
  if (currentSessionId) window.location.href = `/brief/pdf/${currentSessionId}`;
});
