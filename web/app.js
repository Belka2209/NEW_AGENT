const sessionsEl = document.getElementById("sessions");
const messagesEl = document.getElementById("messages");
const form = document.getElementById("form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");
const modelLabel = document.getElementById("model-label");

let currentId = null;
let sending = false;

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function looksLikeToolJson(text) {
  const trimmed = String(text).trim();
  return trimmed.startsWith("{") && trimmed.includes('"name"') && trimmed.includes('"arguments"');
}

function scrollToLatest() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function addBubble(role, text, extraClass) {
  const node = document.createElement("div");
  node.className = `bubble ${role}`;
  if (extraClass) node.classList.add(extraClass);
  node.textContent = text;
  messagesEl.appendChild(node);
  scrollToLatest();
  return node;
}

function showEmpty() {
  messagesEl.innerHTML = `
    <div class="empty">
      Напишите задачу — агент может искать в сети, читать и писать файлы в workspace/ и запускать команды.
    </div>`;
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || response.statusText);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    modelLabel.textContent = data.model || "модель не задана";
    if (data.ok) {
      statusEl.className = "status ok";
      const present = (data.models || []).includes(data.model);
      statusEl.textContent = present
        ? `Ollama подключена · ${data.model}`
        : `Ollama есть, модели ${data.model} нет. Скачайте: ollama pull ${data.model}`;
    } else {
      statusEl.className = "status bad";
      statusEl.textContent = "Ollama недоступна. Запустите её на этом компьютере.";
    }
  } catch (err) {
    statusEl.className = "status bad";
    statusEl.textContent = `Сервер не отвечает: ${err.message}`;
  }
}

function renderSessions(items) {
  sessionsEl.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `session${item.id === currentId ? " active" : ""}`;
    row.innerHTML = `<span>${escapeHtml(item.title)}</span>`;
    const del = document.createElement("button");
    del.className = "ghost";
    del.type = "button";
    del.textContent = "×";
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      await api(`/api/sessions/${item.id}`, { method: "DELETE" });
      if (currentId === item.id) currentId = null;
      await refreshSessions();
    });
    row.appendChild(del);
    row.addEventListener("click", () => openSession(item.id));
    sessionsEl.appendChild(row);
  }
}

async function refreshSessions({ reopen = true } = {}) {
  const items = await api("/api/sessions");
  if (!currentId && items.length) currentId = items[0].id;
  renderSessions(items);
  if (!reopen) return;
  if (currentId) await openSession(currentId);
  else showEmpty();
}

function displayMessage(msg) {
  if (msg.role === "user") {
    addBubble("user", msg.content);
    return;
  }
  if (msg.role === "assistant" && msg.content && !looksLikeToolJson(msg.content)) {
    addBubble("assistant", msg.content);
  }
}

async function openSession(id) {
  currentId = id;
  const items = await api("/api/sessions");
  renderSessions(items);
  const messages = await api(`/api/sessions/${id}/messages`);
  messagesEl.innerHTML = "";
  if (!messages.length) {
    showEmpty();
    return;
  }
  messages.forEach(displayMessage);
  scrollToLatest();
}

async function newChat() {
  const session = await api("/api/sessions", { method: "POST" });
  currentId = session.id;
  await refreshSessions();
  input.focus();
}

async function sendMessage(text) {
  if (!text.trim() || sending) return;
  sending = true;
  sendBtn.disabled = true;

  if (!currentId) {
    const session = await api("/api/sessions", { method: "POST" });
    currentId = session.id;
  }

  const empty = messagesEl.querySelector(".empty");
  if (empty) empty.remove();
  addBubble("user", text);
  const assistant = addBubble("assistant", "Секунду, я думаю…", "thinking");
  let gotText = false;

  try {
    const response = await fetch(`/api/sessions/${currentId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });
    if (!response.ok || !response.body) {
      throw new Error(await response.text());
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data:"));
        if (!line) continue;
        const event = JSON.parse(line.slice(5).trim());
        if (event.type === "token") {
          if (!gotText) {
            assistant.textContent = "";
            assistant.classList.remove("thinking");
            gotText = true;
          }
          assistant.textContent += event.text;
          scrollToLatest();
        } else if (event.type === "tool_start" || event.type === "tool_result") {
          if (!gotText) {
            assistant.textContent = "Секунду, я думаю…";
            assistant.classList.add("thinking");
          }
        } else if (event.type === "title") {
          renderSessions(await api("/api/sessions"));
        } else if (event.type === "error") {
          addBubble("error", event.message);
        }
      }
    }
    if (!gotText) assistant.remove();
  } catch (err) {
    addBubble("error", err.message);
  } finally {
    sending = false;
    sendBtn.disabled = false;
    await refreshSessions({ reopen: false });
    scrollToLatest();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value;
  input.value = "";
  await sendMessage(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.getElementById("new-chat").addEventListener("click", newChat);

loadHealth();
refreshSessions().catch((err) => {
  statusEl.className = "status bad";
  statusEl.textContent = err.message;
});
