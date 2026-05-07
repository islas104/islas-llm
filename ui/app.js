let messages = [];
let currentController = null;
let isStreaming = false;
let userScrolled = false;

// Markdown renderer with syntax-highlighted code blocks
const renderer = new marked.Renderer();
renderer.code = ({ text, lang }) => {
  const language = hljs.getLanguage(lang) ? lang : 'plaintext';
  const highlighted = hljs.highlight(text, { language }).value;
  const safeLang = lang ? lang.replace(/[^a-zA-Z0-9+#-]/g, '') : '';
  return (
    `<div class="code-block">` +
    `<div class="code-header"><span class="code-lang">${safeLang}</span>` +
    `<button class="copy-btn" data-action="copy">Copy</button></div>` +
    `<pre><code class="hljs language-${language}">${highlighted}</code></pre>` +
    `</div>`
  );
};
renderer.link = ({ href, text }) => {
  const safe = encodeURI(href).replace(/['"<>]/g, '');
  return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${text}</a>`;
};

marked.use({ renderer, breaks: true, gfm: true });

const SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li',
    'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'hr', 'button',
  ],
  ALLOWED_ATTR: ['class', 'href', 'target', 'rel', 'data-action'],
};

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text), SANITIZE_CONFIG);
}

function isAtBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scrollToBottom(el, force = false) {
  if (force || !userScrolled) {
    el.scrollTop = el.scrollHeight;
  }
}

function addMessage(role, html = '', asHTML = false) {
  const container = document.getElementById('messages');
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;

  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? 'You' : 'Forge';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (asHTML) bubble.innerHTML = html;
  else bubble.textContent = html;

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  container.appendChild(wrap);
  scrollToBottom(container, true);
  return bubble;
}

function newChat() {
  if (isStreaming && currentController) currentController.abort();
  messages = [];
  document.getElementById('messages').innerHTML = '';
  document.getElementById('input').focus();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

async function sendMessage() {
  if (isStreaming) return;
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';
  userScrolled = false;

  messages.push({ role: 'user', content: text });
  addMessage('user', text);

  const bubble = addMessage('assistant', '', false);
  bubble.classList.add('cursor');

  const sendBtn = document.getElementById('send-btn');
  const stopBtn = document.getElementById('stop-btn');
  sendBtn.classList.add('hidden');
  stopBtn.classList.remove('hidden');
  isStreaming = true;
  currentController = new AbortController();

  const container = document.getElementById('messages');
  let fullText = '';

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
      signal: currentController.signal,
    });

    if (res.status === 503) {
      bubble.textContent = 'Model is busy — please wait a moment and try again.';
      bubble.classList.add('error');
      return;
    }
    if (res.status === 429) {
      bubble.textContent = 'Rate limit reached — please slow down.';
      bubble.classList.add('error');
      return;
    }
    if (!res.ok) {
      bubble.textContent = `Error ${res.status} — please try again.`;
      bubble.classList.add('error');
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      fullText += decoder.decode(value, { stream: true });
      bubble.innerHTML = renderMarkdown(fullText);
      scrollToBottom(container);
    }

    messages.push({ role: 'assistant', content: fullText });

    const history = document.getElementById('history');
    const item = document.createElement('div');
    item.className = 'history-item';
    item.textContent = text.slice(0, 40) + (text.length > 40 ? '…' : '');
    history.prepend(item);

  } catch (err) {
    if (err.name === 'AbortError') {
      if (fullText) messages.push({ role: 'assistant', content: fullText });
    } else {
      bubble.textContent = 'Connection error — is the server running?';
      bubble.classList.add('error');
    }
  } finally {
    bubble.classList.remove('cursor');
    if (fullText) bubble.innerHTML = renderMarkdown(fullText);
    sendBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
    isStreaming = false;
    currentController = null;
  }
}

function init() {
  const input = document.getElementById('input');
  const messagesEl = document.getElementById('messages');

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  input.addEventListener('input', () => autoResize(input));

  document.getElementById('send-btn').addEventListener('click', sendMessage);
  document.getElementById('stop-btn').addEventListener('click', () => currentController?.abort());
  document.getElementById('new-chat').addEventListener('click', newChat);

  messagesEl.addEventListener('scroll', () => { userScrolled = !isAtBottom(messagesEl); });

  // Copy button via event delegation — no inline handlers needed
  messagesEl.addEventListener('click', e => {
    const btn = e.target.closest('[data-action="copy"]');
    if (!btn) return;
    const code = btn.closest('.code-block')?.querySelector('code')?.innerText ?? '';
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => (btn.textContent = 'Copy'), 2000);
    });
  });

  input.focus();
}

document.addEventListener('DOMContentLoaded', init);
