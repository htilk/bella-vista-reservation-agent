"use strict";

const transcript = document.getElementById("transcript");
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const resetBtn = document.getElementById("reset-btn");

const CODE_RE = /\bBV-[A-Z0-9]{4}\b/g;

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Render agent text, turning confirmation codes (BV-XXXX) into monospace badges.
function withCodeBadges(text) {
  return escapeHtml(text).replace(CODE_RE, (m) => `<span class="code">${m}</span>`);
}

function addMessage(role, text, { asHtml = false } = {}) {
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (asHtml) bubble.innerHTML = text;
  else bubble.textContent = text;
  msg.appendChild(bubble);
  transcript.appendChild(msg);
  scrollToBottom();
  return msg;
}

function addTypingIndicator() {
  const msg = document.createElement("div");
  msg.className = "msg agent typing";
  msg.innerHTML = `<div class="bubble"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>`;
  transcript.appendChild(msg);
  scrollToBottom();
  return msg;
}

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.disabled = busy;
  if (!busy) input.focus();
}

async function sendMessage(text) {
  addMessage("guest", text);
  setBusy(true);
  const typing = addTypingIndicator();

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    typing.remove();
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      addMessage("error", `Sorry — something went wrong (${res.status}). ${detail}`.trim());
      return;
    }
    const data = await res.json();
    addMessage("agent", withCodeBadges(data.reply || ""), { asHtml: true });
  } catch (err) {
    typing.remove();
    addMessage("error", "I couldn't reach the server. Please check your connection and try again.");
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});

resetBtn.addEventListener("click", async () => {
  try {
    await fetch("/api/reset", { method: "POST" });
  } catch (_) {
    /* ignore — still clear the UI */
  }
  transcript.innerHTML = "";
  greet();
  input.focus();
});

function greet() {
  addMessage(
    "agent",
    "Hi! I'm the Bella Vista reservations assistant. I can book a table, change or cancel a reservation, or check availability. What can I do for you?"
  );
}

greet();
