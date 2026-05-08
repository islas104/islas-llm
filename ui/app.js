// ── Markdown setup ──────────────────────────────────────────────────────────
const _renderer = new marked.Renderer();

_renderer.code = ({ text, lang }) => {
  const language = hljs.getLanguage(lang) ? lang : 'plaintext';
  const highlighted = hljs.highlight(text, { language }).value;
  const safeLang = lang ? lang.replace(/[^a-zA-Z0-9+#.-]/g, '') : '';
  return (
    `<div class="code-block">` +
    `<div class="code-header"><span class="code-lang">${safeLang}</span>` +
    `<button class="copy-btn" data-action="copy">Copy</button></div>` +
    `<pre><code class="hljs language-${language}">${highlighted}</code></pre></div>`
  );
};

_renderer.link = ({ href, text }) => {
  const safe = encodeURI(href).replace(/['"<>]/g, '');
  return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${text}</a>`;
};

marked.use({ renderer: _renderer, breaks: true, gfm: true });

const _SANITIZE = {
  ALLOWED_TAGS: ['p','br','strong','em','code','pre','ul','ol','li','blockquote',
                 'h1','h2','h3','h4','h5','h6','a','table','thead','tbody','tr',
                 'th','td','div','span','hr','button'],
  ALLOWED_ATTR: ['class','href','target','rel','data-action'],
};

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text), _SANITIZE);
}

// ── State ───────────────────────────────────────────────────────────────────
const state = {
  convId: null,
  ws: null,
  generating: false,
  userScrolled: false,
  activeBubble: null,
  settings: { temperature: 0.7, maxTokens: 512 },
};

// ── WebSocket ────────────────────────────────────────────────────────────────
let _wsQueue = [];
let _pingInterval = null;

function setStatus(text, ok = true) {
  const bar = document.getElementById('status-bar');
  bar.textContent = text;
  bar.className = `status-bar visible ${ok ? 'ok' : 'err'}`;
  if (ok) setTimeout(() => bar.classList.remove('visible'), 2000);
}

function connectWs(convId) {
  if (state.ws) { state.ws.onclose = null; state.ws.close(); }
  if (_pingInterval) { clearInterval(_pingInterval); _pingInterval = null; }
  _wsQueue = [];

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/${convId}`);
  state.ws = ws;

  ws.onopen = () => {
    setStatus('Connected', true);
    _wsQueue.forEach(d => ws.send(JSON.stringify(d)));
    _wsQueue = [];
    _pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 25000);
  };

  ws.onmessage = e => handleWsEvent(JSON.parse(e.data));

  ws.onerror = () => setStatus('Connection error — retrying…', false);

  ws.onclose = () => {
    if (state.generating) endGeneration();
    if (state.convId === convId) {
      setStatus('Disconnected — reconnecting…', false);
      setTimeout(() => { if (state.convId === convId) connectWs(convId); }, 2000);
    }
  };
}

function wsSend(data) {
  if (!state.ws) return;
  if (state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(data));
  } else if (state.ws.readyState === WebSocket.CONNECTING) {
    _wsQueue.push(data);  // send once connection opens
  }
}

const _wsHandlers = {
  message_saved(data) {
    const wrap = state.activeBubble?.closest('.message');
    if (wrap) wrap.dataset.msgId = data.message.id;
  },
  token(data) {
    if (!state.activeBubble) return;
    const isFirst = !state.activeBubble._raw;
    state.activeBubble._raw = (state.activeBubble._raw || '') + data.content;
    if (isFirst) state.activeBubble.classList.add('cursor');
    state.activeBubble.innerHTML = renderMarkdown(state.activeBubble._raw);
    scrollToBottom();
  },
  done(data) {
    if (state.activeBubble) {
      const wrap = state.activeBubble.closest('.message');
      if (wrap) wrap.dataset.msgId = data.message.id;
      finaliseAssistantMessage(state.activeBubble);
    }
    endGeneration();
  },
  title_updated(data) {
    updateSidebarTitle(state.convId, data.title);
  },
  error(data) {
    if (state.activeBubble) {
      state.activeBubble.textContent = data.message;
      state.activeBubble.classList.add('error');
    }
    endGeneration();
  },
};

function handleWsEvent(data) {
  _wsHandlers[data.type]?.(data);
}

// ── Conversations ─────────────────────────────────────────────────────────────
function relativeTime(ms) {
  const d = Date.now() - ms;
  if (d < 60_000)      return 'just now';
  if (d < 3_600_000)   return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000)  return `${Math.floor(d / 3_600_000)}h ago`;
  if (d < 604_800_000) return `${Math.floor(d / 86_400_000)}d ago`;
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

async function loadConversations() {
  const convs = await apiFetch('/api/conversations');
  renderSidebar(convs);
}

function renderSidebar(convs) {
  const list = document.getElementById('conv-list');
  list.innerHTML = '';
  convs.forEach(c => list.appendChild(makeConvItem(c)));
  applySearch(document.getElementById('conv-search')?.value ?? '');
}

function makeConvItem(conv) {
  const item = document.createElement('div');
  item.className = 'conv-item' + (conv.id === state.convId ? ' active' : '');
  item.dataset.id = conv.id;

  const meta = document.createElement('div');
  meta.className = 'conv-meta';

  const title = document.createElement('span');
  title.className = 'conv-title';
  title.textContent = conv.title;

  const time = document.createElement('span');
  time.className = 'conv-time';
  time.textContent = relativeTime(conv.updated_at);

  meta.appendChild(title);
  meta.appendChild(time);

  const del = document.createElement('button');
  del.className = 'conv-delete';
  del.title = 'Delete';
  del.textContent = '✕';
  del.addEventListener('click', e => { e.stopPropagation(); deleteConversation(conv.id); });

  item.appendChild(meta);
  item.appendChild(del);
  item.addEventListener('click', () => openConversation(conv.id));
  return item;
}

function applySearch(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll('.conv-item').forEach(item => {
    const title = item.querySelector('.conv-title').textContent.toLowerCase();
    item.classList.toggle('hidden', q.length > 0 && !title.includes(q));
  });
}

function updateSidebarTitle(convId, title) {
  const item = document.querySelector(`.conv-item[data-id="${convId}"] .conv-title`);
  if (item) item.textContent = title;
}

async function newConversation() {
  const conv = await apiFetch('/api/conversations', { method: 'POST', json: {} });
  await loadConversations();
  await openConversation(conv.id);
}

async function openConversation(convId) {
  if (state.generating) stopGeneration();
  state.convId = convId;
  document.querySelector('.sidebar')?.classList.remove('open');
  document.getElementById('mobile-overlay')?.classList.remove('visible');

  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === convId);
  });

  try {
    const data = await apiFetch(`/api/conversations/${convId}`);
    if (!data) return;
    renderConversation(data);
    connectWs(convId);
    loadSettingsFromConv(data);
  } catch (err) {
    setStatus(`Failed to load conversation: ${err.message}`, false);
  }
}

function renderConversation(data) {
  const container = document.getElementById('messages');
  container.innerHTML = '';
  document.getElementById('empty-state')?.remove();

  data.messages.forEach(m => {
    const bubble = addMessageEl(m.role, m.id);
    if (m.role === 'assistant') {
      bubble.innerHTML = renderMarkdown(m.content);
      attachAssistantActions(bubble);
    } else {
      bubble.textContent = m.content;
      attachUserActions(bubble, m.id, m.content);
    }
  });
  scrollToBottom(true);
}

async function deleteConversation(convId) {
  await apiFetch(`/api/conversations/${convId}`, { method: 'DELETE' });
  if (state.convId === convId) {
    if (state.ws) state.ws.close();
    state.convId = null;
    document.getElementById('messages').innerHTML =
      '<div class="empty-state" id="empty-state"><div class="empty-logo">⚡</div>' +
      '<div class="empty-title">Islas LLM</div><div class="empty-sub">How can I help you today?</div>' +
      '<div class="suggestions">' +
      '<button class="suggestion">Explain a concept simply</button>' +
      '<button class="suggestion">Write or review code</button>' +
      '<button class="suggestion">Help me debug a problem</button>' +
      '<button class="suggestion">Help with writing or editing</button>' +
      '</div></div>';
    document.querySelectorAll('.suggestion').forEach(btn => {
      btn.addEventListener('click', () => sendMessage(btn.textContent.trim()));
    });
  }
  await loadConversations();
}

// ── Messaging ────────────────────────────────────────────────────────────────
async function sendMessage(content, truncateFromId = null) {
  if (state.generating) return;
  if (!state.convId) {
    try { await newConversation(); } catch (err) {
      setStatus(`Could not start conversation: ${err.message}`, false);
      return;
    }
  }

  const userBubble = addMessageEl('user');
  userBubble.textContent = content;
  attachUserActions(userBubble, null, content);

  const asstBubble = addMessageEl('assistant');
  asstBubble._raw = '';
  asstBubble.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  state.activeBubble = asstBubble;

  beginGeneration();
  state.userScrolled = false;

  wsSend({
    type: 'message',
    content,
    max_tokens: state.settings.maxTokens,
    temperature: state.settings.temperature,
    ...(truncateFromId ? { truncate_from_id: truncateFromId } : {}),
  });
}

function stopGeneration() {
  wsSend({ type: 'stop' });
  endGeneration();
}

function beginGeneration() {
  state.generating = true;
  document.getElementById('send-btn').classList.add('hidden');
  document.getElementById('stop-btn').classList.remove('hidden');
}

function endGeneration() {
  state.generating = false;
  if (state.activeBubble) {
    state.activeBubble.classList.remove('cursor');
    state.activeBubble = null;
  }
  document.getElementById('stop-btn').classList.add('hidden');
  document.getElementById('send-btn').classList.remove('hidden');
}

// ── Message elements ─────────────────────────────────────────────────────────
function addMessageEl(role, msgId = null) {
  const empty = document.getElementById('empty-state');
  if (empty) empty.remove();

  const container = document.getElementById('messages');
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  if (msgId) wrap.dataset.msgId = msgId;

  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? 'You' : 'Islas LLM';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  container.appendChild(wrap);
  scrollToBottom(true);
  return bubble;
}

function attachUserActions(bubble, msgId, content) {
  const wrap = bubble.closest('.message');
  const actions = document.createElement('div');
  actions.className = 'msg-actions';

  const editBtn = document.createElement('button');
  editBtn.className = 'msg-action-btn';
  editBtn.textContent = 'Edit';
  editBtn.addEventListener('click', () => startEdit(wrap, msgId, content));

  actions.appendChild(editBtn);
  wrap.appendChild(actions);
}

function attachAssistantActions(bubble) {
  const wrap = bubble.closest('.message');
  const actions = document.createElement('div');
  actions.className = 'msg-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'msg-action-btn';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(bubble._raw || bubble.innerText);
    copyBtn.textContent = 'Copied!';
    setTimeout(() => (copyBtn.textContent = 'Copy'), 2000);
  });

  const regenBtn = document.createElement('button');
  regenBtn.className = 'msg-action-btn';
  regenBtn.textContent = 'Regenerate';
  regenBtn.addEventListener('click', () => regenerate(wrap));

  actions.appendChild(copyBtn);
  actions.appendChild(regenBtn);
  wrap.appendChild(actions);
}

function finaliseAssistantMessage(bubble) {
  bubble._raw = bubble._raw || bubble.innerText;
  bubble.innerHTML = renderMarkdown(bubble._raw);
  attachAssistantActions(bubble);
}

// ── Edit & Regenerate ─────────────────────────────────────────────────────────
function startEdit(msgWrap, msgId, originalContent) {
  const bubble = msgWrap.querySelector('.bubble');
  const textarea = document.createElement('textarea');
  textarea.value = originalContent;
  textarea.className = 'bubble';
  textarea.style.cssText = 'width:100%;min-height:80px;resize:vertical;';

  const actions = msgWrap.querySelector('.msg-actions');
  const confirmBtn = document.createElement('button');
  confirmBtn.className = 'msg-action-btn';
  confirmBtn.textContent = 'Send';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'msg-action-btn';
  cancelBtn.textContent = 'Cancel';

  cancelBtn.addEventListener('click', () => {
    msgWrap.replaceChild(bubble, textarea);
    actions.innerHTML = '';
    attachUserActions(bubble, msgId, originalContent);
  });

  confirmBtn.addEventListener('click', async () => {
    const newContent = textarea.value.trim();
    if (!newContent) return;
    // Remove this message and everything after it, then resend
    const allMessages = [...document.getElementById('messages').querySelectorAll('.message')];
    const idx = allMessages.indexOf(msgWrap);
    allMessages.slice(idx).forEach(el => el.remove());
    sendMessage(newContent, msgId);
  });

  msgWrap.replaceChild(textarea, bubble);
  actions.innerHTML = '';
  actions.appendChild(confirmBtn);
  actions.appendChild(cancelBtn);
  textarea.focus();
}

function regenerate(asstWrap) {
  // Find the user message immediately before this one
  const allMessages = [...document.getElementById('messages').querySelectorAll('.message')];
  const idx = allMessages.indexOf(asstWrap);
  if (idx <= 0) return;

  const userWrap = allMessages[idx - 1];
  const userMsgId = userWrap.dataset.msgId;
  const userContent = userWrap.querySelector('.bubble').textContent;

  // Remove from userWrap onwards in the DOM
  allMessages.slice(idx - 1).forEach(el => el.remove());
  sendMessage(userContent, userMsgId);
}

// ── Settings ─────────────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById('settings-panel').classList.remove('hidden');
  document.getElementById('settings-overlay').classList.remove('hidden');
}

function closeSettings() {
  document.getElementById('settings-panel').classList.add('hidden');
  document.getElementById('settings-overlay').classList.add('hidden');
}

function loadSettingsFromConv(conv) {
  document.getElementById('system-prompt').value = conv.system_prompt || '';
  document.getElementById('temperature').value = state.settings.temperature;
  document.getElementById('max-tokens').value = state.settings.maxTokens;
  document.getElementById('temp-val').textContent = state.settings.temperature;
}

async function saveSettings() {
  const systemPrompt = document.getElementById('system-prompt').value.trim();
  const temperature = parseFloat(document.getElementById('temperature').value);
  const maxTokens = parseInt(document.getElementById('max-tokens').value, 10);

  state.settings.temperature = temperature;
  state.settings.maxTokens = maxTokens;

  if (state.convId) {
    await apiFetch(`/api/conversations/${state.convId}`, {
      method: 'PATCH',
      json: { system_prompt: systemPrompt },
    });
  }

  closeSettings();
}

// ── Scroll ───────────────────────────────────────────────────────────────────
function scrollToBottom(force = false) {
  const el = document.getElementById('messages');
  if (force || !state.userScrolled) {
    el.scrollTop = el.scrollHeight;
    document.getElementById('scroll-btn')?.classList.remove('visible');
    state.userScrolled = false;
  }
}

// ── Export ───────────────────────────────────────────────────────────────────
function exportConversation() {
  const msgs = [...document.querySelectorAll('#messages .message')];
  if (!msgs.length) return;
  const title = document.querySelector('.conv-item.active .conv-title')?.textContent || 'conversation';
  const lines = [`# ${title}`, ''];
  msgs.forEach(m => {
    const isUser = m.classList.contains('user');
    const bubble = m.querySelector('.bubble');
    const content = (isUser ? bubble.textContent : (bubble._raw || bubble.innerText)).trim();
    lines.push(`**${isUser ? 'You' : 'Islas LLM'}**`, '', content, '');
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = title.replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 50) + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Helpers ──────────────────────────────────────────────────────────────────
async function apiFetch(url, opts = {}) {
  const { method = 'GET', json } = opts;
  const res = await fetch(url, {
    method,
    headers: json ? { 'Content-Type': 'application/json' } : {},
    body: json ? JSON.stringify(json) : undefined,
  });
  if (res.status === 401) { location.href = '/login'; return null; }
  if (!res.ok) throw new Error(`${method} ${url} → ${res.status}`);
  return res.json();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

// ── Init ─────────────────────────────────────────────────────────────────────
function init() {
  const input = document.getElementById('input');
  const messages = document.getElementById('messages');

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const text = input.value.trim();
      if (text) { input.value = ''; input.style.height = 'auto'; sendMessage(text); }
    }
  });
  input.addEventListener('input', () => {
    autoResize(input);
    const len = input.value.length;
    const counter = document.getElementById('char-count');
    if (len > 0) {
      counter.textContent = `${len.toLocaleString()} / 8,000`;
      counter.classList.toggle('over', len > 8000);
      counter.classList.remove('hidden');
    } else {
      counter.classList.add('hidden');
    }
  });

  document.getElementById('send-btn').addEventListener('click', () => {
    const text = input.value.trim();
    if (text) { input.value = ''; input.style.height = 'auto'; sendMessage(text); }
  });
  document.getElementById('stop-btn').addEventListener('click', stopGeneration);
  document.getElementById('new-chat-btn').addEventListener('click', newConversation);
  document.getElementById('settings-btn').addEventListener('click', openSettings);
  document.getElementById('close-settings-btn').addEventListener('click', closeSettings);
  document.getElementById('settings-overlay').addEventListener('click', closeSettings);
  document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
  document.getElementById('logout-btn').addEventListener('click', async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    location.href = '/login';
  });

  document.getElementById('temperature').addEventListener('input', e => {
    document.getElementById('temp-val').textContent = parseFloat(e.target.value).toFixed(2);
  });

  messages.addEventListener('scroll', () => {
    state.userScrolled = messages.scrollHeight - messages.scrollTop - messages.clientHeight > 80;
    document.getElementById('scroll-btn').classList.toggle('visible', state.userScrolled);
  });

  // Copy code blocks via event delegation
  messages.addEventListener('click', e => {
    const btn = e.target.closest('[data-action="copy"]');
    if (!btn) return;
    const code = btn.closest('.code-block')?.querySelector('code')?.innerText ?? '';
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => (btn.textContent = 'Copy'), 2000);
    });
  });

  // Suggested prompts
  document.querySelectorAll('.suggestion').forEach(btn => {
    btn.addEventListener('click', () => sendMessage(btn.textContent.trim()));
  });

  // Search
  document.getElementById('conv-search').addEventListener('input', e => {
    applySearch(e.target.value);
  });

  // Export
  document.getElementById('export-btn').addEventListener('click', exportConversation);

  // Scroll to bottom
  document.getElementById('scroll-btn').addEventListener('click', () => scrollToBottom(true));

  // Keyboard shortcuts
  document.addEventListener('keydown', e => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key === 'k') { e.preventDefault(); newConversation(); }
    if (mod && e.key === '/') { e.preventDefault(); document.getElementById('conv-search').focus(); }
    if (e.key === 'Escape' && !document.getElementById('settings-panel').classList.contains('hidden')) {
      closeSettings();
    }
  });

  // Mobile sidebar toggle
  const sidebar = document.querySelector('.sidebar');
  const mobileOverlay = document.getElementById('mobile-overlay');
  function closeMobileSidebar() {
    sidebar.classList.remove('open');
    mobileOverlay.classList.remove('visible');
  }
  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    mobileOverlay.classList.toggle('visible');
  });
  mobileOverlay?.addEventListener('click', closeMobileSidebar);

  loadConversations();
  input.focus();
}

document.addEventListener('DOMContentLoaded', init);
