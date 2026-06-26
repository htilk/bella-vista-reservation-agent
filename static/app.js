"use strict";

const transcript = document.getElementById("transcript");
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const resetBtn = document.getElementById("reset-btn");
const charCount = document.getElementById("char-count");

const CODE_RE = /\bBV-[A-Z0-9]{4}\b/g;
const MAX_LEN = Number(input.getAttribute("maxlength")) || 2000;
const ROLE_AVATAR = { agent: "🍝", guest: "🙂", error: "⚠️" };

let lastFailedText = null;

function timeLabel() {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// Render agent text into `bubble`, turning confirmation codes (BV-XXXX) into
// clickable, copyable monospace badges. Built entirely from DOM nodes — no
// innerHTML for model output — so the agent's text can never inject markup.
function renderAgentText(bubble, text) {
  let last = 0;
  text.replace(CODE_RE, (match, offset) => {
    if (offset > last) bubble.appendChild(document.createTextNode(text.slice(last, offset)));
    bubble.appendChild(makeCodeBadge(match));
    last = offset + match.length;
    return match;
  });
  if (last < text.length) bubble.appendChild(document.createTextNode(text.slice(last)));
}

function makeCodeBadge(code) {
  const span = document.createElement("button");
  span.type = "button";
  span.className = "code";
  span.textContent = code;
  span.title = "Click to copy";
  span.setAttribute("aria-label", `Confirmation code ${code}, click to copy`);
  span.addEventListener("click", () => copyCode(span, code));
  return span;
}

async function copyCode(el, code) {
  try {
    await navigator.clipboard.writeText(code);
    el.classList.add("copied");
    const prev = el.textContent;
    el.textContent = "Copied!";
    setTimeout(() => {
      el.textContent = prev;
      el.classList.remove("copied");
    }, 1000);
  } catch (_) {
    /* clipboard blocked (e.g. insecure context) — ignore */
  }
}

function addMessage(role, text, { html = false } = {}) {
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = ROLE_AVATAR[role] || "•";

  const col = document.createElement("div");
  col.className = "col";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (html === "agent") renderAgentText(bubble, text);
  else bubble.textContent = text;

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = timeLabel();

  col.appendChild(bubble);
  col.appendChild(meta);
  msg.appendChild(avatar);
  msg.appendChild(col);
  transcript.appendChild(msg);
  scrollToBottom();
  return { msg, bubble };
}

function addTypingIndicator() {
  const msg = document.createElement("div");
  msg.className = "msg agent typing";
  msg.innerHTML =
    `<div class="avatar" aria-hidden="true">🍝</div>` +
    `<div class="col"><div class="bubble"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div></div>`;
  transcript.appendChild(msg);
  scrollToBottom();
  return msg;
}

function addErrorWithRetry(text, retryText) {
  const { bubble } = addMessage("error", text);
  if (retryText == null) return;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "retry";
  retry.textContent = "Retry";
  retry.addEventListener("click", () => {
    retry.disabled = true;
    sendMessage(retryText);
  });
  bubble.appendChild(document.createElement("br"));
  bubble.appendChild(retry);
}

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.disabled = busy;
  if (!busy) input.focus();
}

function updateCharCount() {
  if (!charCount) return;
  const n = input.value.length;
  charCount.textContent = `${n}/${MAX_LEN}`;
  charCount.classList.toggle("near-limit", n > MAX_LEN * 0.9);
}

async function sendMessage(text) {
  lastFailedText = null;
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
    const data = await res.json().catch(() => ({}));
    if (!res.ok && !data.reply) {
      lastFailedText = text;
      addErrorWithRetry(`Sorry — something went wrong (${res.status}).`, text);
      return;
    }
    addMessage("agent", data.reply || "", { html: "agent" });
  } catch (err) {
    typing.remove();
    lastFailedText = text;
    addErrorWithRetry("I couldn't reach the server. Please check your connection.", text);
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  updateCharCount();
  sendMessage(text);
});

input.addEventListener("input", updateCharCount);

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

// Quick-reply chips: clicking one sends it as the guest's message.
function addChips(labels) {
  const wrap = document.createElement("div");
  wrap.className = "chips";
  wrap.setAttribute("aria-label", "Quick actions");
  labels.forEach((label) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = label;
    chip.addEventListener("click", () => {
      wrap.remove();
      sendMessage(label);
    });
    wrap.appendChild(chip);
  });
  transcript.appendChild(wrap);
  scrollToBottom();
}

function greet() {
  addMessage(
    "agent",
    "Hi! I'm the Bella Vista reservations assistant. I can book a table, change or cancel a reservation, or check availability. What can I do for you?"
  );
  addChips([
    "Book a table",
    "Check availability",
    "Change a reservation",
    "Cancel a reservation",
  ]);
}

updateCharCount();
greet();
