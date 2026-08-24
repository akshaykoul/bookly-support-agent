// Bookly chat + voice UI.
//
// Voice is deliberately a thin layer on top of the same text pipeline: the
// mic just transcribes speech into the same input box a typed message would
// go through (Web Speech API SpeechRecognition), and "speak responses" just
// reads the same reply text back out loud (speechSynthesis). No separate
// voice backend, no extra API keys -- the agent core never knows whether a
// message originated as speech or typing.
//
// The passcode gate below is a lightweight guard for a publicly hosted demo
// link (see BOOKLY_ACCESS_CODE in the backend) -- not real auth. It's a
// documented scope decision, not a security feature: it just keeps random
// crawlers/bots off a URL that calls a real, billed LLM API.

let sessionId = null;
let accessCode = sessionStorage.getItem("bookly_access_code") || "";
let appShown = false;
let mode = "chat"; // "chat" or "voice" -- mirrors the Claude/ChatGPT app mode switch

const gateEl = document.getElementById("gate");
const gateForm = document.getElementById("gateForm");
const gateInput = document.getElementById("gateInput");
const gateError = document.getElementById("gateError");
const appEl = document.getElementById("app");

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chatForm");
const inputEl = document.getElementById("messageInput");
const micBtn = document.getElementById("micBtn");
const voicePanelEl = document.getElementById("voicePanel");
const voiceStatusEl = document.getElementById("voiceStatus");
const modeChatBtn = document.getElementById("modeChatBtn");
const modeVoiceBtn = document.getElementById("modeVoiceBtn");
const sessionLabel = document.getElementById("sessionIdLabel");
const traceLink = document.getElementById("traceLink");

// Login-style handoff: fade the gate out, then swap it for the app rather
// than an instant show/hide -- and guard against ever running this twice
// (e.g. a stored access code passing silently while a stale gate submit is
// also in flight).
function showApp() {
  if (appShown) return;
  appShown = true;
  gateEl.classList.add("leaving");
  setTimeout(() => {
    gateEl.hidden = true;
  }, 220);
  appEl.hidden = false;
  appEl.classList.add("entering");
  updateSessionLabel();
  addMessage(
    "system",
    "Hi! I'm the Bookly support assistant. Ask about an order, start a return, or ask a general question."
  );
}

function setMode(next) {
  mode = next;
  const isVoice = mode === "voice";
  modeChatBtn.classList.toggle("active", !isVoice);
  modeVoiceBtn.classList.toggle("active", isVoice);
  modeChatBtn.setAttribute("aria-selected", String(!isVoice));
  modeVoiceBtn.setAttribute("aria-selected", String(isVoice));
  formEl.hidden = isVoice;
  voicePanelEl.hidden = !isVoice;
  if (!isVoice && "speechSynthesis" in window) window.speechSynthesis.cancel();
}
modeChatBtn.addEventListener("click", () => setMode("chat"));
modeVoiceBtn.addEventListener("click", () => setMode("voice"));

async function tryEnterWithCode(code) {
  const res = await fetch("/verify-code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code || "" }),
  });
  const data = await res.json();
  if (!data.gate_enabled) {
    // No BOOKLY_ACCESS_CODE configured server-side -- gate is off entirely.
    return true;
  }
  return data.ok === true;
}

(async function init() {
  if (accessCode) {
    const ok = await tryEnterWithCode(accessCode);
    if (ok) {
      showApp();
      return;
    }
    sessionStorage.removeItem("bookly_access_code");
    accessCode = "";
  } else {
    // Check whether the gate is even enabled before making the user look at it.
    const openWithNoCode = await tryEnterWithCode("");
    if (openWithNoCode) {
      showApp();
      return;
    }
  }
  gateEl.hidden = false;
})();

gateForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = gateInput.value.trim();
  gateError.textContent = "";
  const ok = await tryEnterWithCode(code);
  if (ok) {
    accessCode = code;
    sessionStorage.setItem("bookly_access_code", code);
    showApp();
  } else {
    gateError.textContent = "That code didn't work. Try again.";
  }
});

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "msg assistant typing";
  div.innerHTML = "<span class=\"dot\"></span><span class=\"dot\"></span><span class=\"dot\"></span>";
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function updateSessionLabel() {
  sessionLabel.textContent = sessionId || "(new)";
  const suffix = accessCode ? `?code=${encodeURIComponent(accessCode)}` : "";
  traceLink.href = sessionId ? `/trace/${sessionId}${suffix}` : "#";
}

async function sendMessage(text) {
  if (!text.trim()) return;
  addMessage("user", text);
  inputEl.value = "";
  const typingEl = showTyping();

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Access-Code": accessCode,
      },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    typingEl.remove();
    if (!res.ok) {
      const body = await res.text();
      addMessage("system", `Error: ${res.status} ${body}`);
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
    updateSessionLabel();
    addMessage("assistant", data.reply);
    if (mode === "voice") speak(data.reply);
  } catch (err) {
    typingEl.remove();
    addMessage("system", `Network error: ${err.message}`);
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(inputEl.value);
});

// --- Voice input (STT) ---
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

if (SpeechRecognitionCtor) {
  recognizer = new SpeechRecognitionCtor();
  recognizer.lang = "en-US";
  recognizer.interimResults = false;
  recognizer.maxAlternatives = 1;

  recognizer.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    inputEl.value = transcript;
    if (voiceStatusEl) voiceStatusEl.textContent = "Tap to speak";
    sendMessage(transcript);
  };
  recognizer.onerror = () => {
    micBtn.classList.remove("listening");
    if (voiceStatusEl) voiceStatusEl.textContent = "Tap to speak";
  };
  recognizer.onend = () => {
    micBtn.classList.remove("listening");
    if (voiceStatusEl) voiceStatusEl.textContent = "Tap to speak";
  };

  micBtn.addEventListener("click", () => {
    micBtn.classList.add("listening");
    if (voiceStatusEl) voiceStatusEl.textContent = "Listening...";
    recognizer.start();
  });
} else {
  micBtn.disabled = true;
  micBtn.title = "Speech recognition isn't supported in this browser (try Chrome)";
  if (voiceStatusEl) voiceStatusEl.textContent = "Voice input isn't supported in this browser (try Chrome)";
}

// --- Voice output (TTS) ---
//
// Tries ElevenLabs first (server-side call, real voice quality) and falls
// back to the browser's free built-in speechSynthesis if ElevenLabs isn't
// configured, the free-tier quota is exhausted, or the request fails for
// any other reason. The chat should never break because of a voice issue.

// Both TTS engines will happily narrate an emoji ("grinning face", a chime,
// etc) if it's in the string -- that's exactly what shows up as "reading
// the emojis out loud". Strip decorative characters before anything is
// spoken; the on-screen chat bubble (already rendered via addMessage before
// speak() is called) keeps the original text untouched.
function stripForSpeech(text) {
  return text
    .replace(/\p{Extended_Pictographic}/gu, "")
    .replace(/[\u200D\uFE0F]/g, "") // zero-width joiner + variation selector remnants
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function speakWithBrowser(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel(); // don't stack overlapping utterances
  const utterance = new SpeechSynthesisUtterance(stripForSpeech(text));
  utterance.rate = 1.0;
  window.speechSynthesis.speak(utterance);
}

async function speak(text) {
  const clean = stripForSpeech(text);
  if (!clean) return;
  try {
    const res = await fetch("/speak", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Access-Code": accessCode,
      },
      body: JSON.stringify({ text: clean }),
    });
    if (res.status === 200) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
      return;
    }
    // 204 = ElevenLabs not configured, or another non-fatal issue -- fall through.
  } catch (err) {
    // network error, etc -- fall through to browser TTS.
  }
  speakWithBrowser(text);
}
