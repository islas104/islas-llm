let messages = [];
let isStreaming = false;

function newChat() {
  messages = [];
  document.getElementById("messages").innerHTML = "";
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function addMessage(role, content = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  const label = document.createElement("div");
  label.className = "label";
  label.textContent = role === "user" ? "You" : "Forge";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  document.getElementById("messages").appendChild(wrap);
  wrap.scrollIntoView({ behavior: "smooth", block: "end" });
  return bubble;
}

async function sendMessage() {
  if (isStreaming) return;

  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  input.style.height = "auto";

  messages.push({ role: "user", content: text });
  addMessage("user", text);

  const bubble = addMessage("assistant");
  bubble.classList.add("cursor");

  document.getElementById("send-btn").disabled = true;
  isStreaming = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });

    if (!res.ok) throw new Error(`Server error: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let full = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      full += chunk;
      bubble.textContent = full;
      bubble.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    messages.push({ role: "assistant", content: full });

    // Add to sidebar history
    const history = document.getElementById("history");
    const item = document.createElement("div");
    item.className = "history-item";
    item.textContent = text.slice(0, 40) + (text.length > 40 ? "…" : "");
    history.prepend(item);

  } catch (err) {
    bubble.textContent = "Error: " + err.message;
  } finally {
    bubble.classList.remove("cursor");
    document.getElementById("send-btn").disabled = false;
    isStreaming = false;
  }
}
