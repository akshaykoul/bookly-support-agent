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

const gateEl = document.getElementById("gate");
const gateForm = document.getElementById("gateForm");
const gateInput = document.getElementById("gateInput");
const gateError = document.getElementById("gateError");
const appEl = document.getElementById("app");

const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chatForm");
const inputEl = document.getElementById("messageInput");
const micBtn = document.getElementById("micBtn");
const voiceToggle = document.getElementById("voiceToggle");
const sessionLabel = document.getElementById("sessionIdLabel");
const traceLink = document.getElementById("traceLink");

function showApp() {
  gateEl.hidden = true;
  appEl.hidden = false;
  updateSessionLabel();
  addMessage(
    "system",
    "Hi! I'm the Bookly support assistant. Ask about an order, start a return, or ask a general question."
  );
}

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

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Access-Code": accessCode,
      },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    if (!res.ok) {
      const body = await res.text();
      addMessage("system", `Error: ${res.status} ${body}`);
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
    updateSessionLabel();
    addMessage("assistant", data.reply);
    if (voiceToggle.checked) speak(data.reply);
  } catch (err) {
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
    sendMessage(transcript);
  };
  recognizer.onerror = () => micBtn.classList.remove("listening");
  recognizer.onend = () => micBtn.classList.remove("listening");

  micBtn.addEventListener("click", () => {
    micBtn.classList.add("listening");
    recognizer.start();
  });
} else {
  micBtn.disabled = true;
  micBtn.title = "Speech recognition isn't supported in this browser (try Chrome)";
}

// --- Voice output (TTS) ---
function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel(); // don't stack overlapping utterances
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  window.speechSynthesis.speak(utterance);
}
