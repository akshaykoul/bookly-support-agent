// Bookly chat + voice UI.
//
// Voice mode is a persistent, hands-free conversation, not a one-shot
// push-to-talk button: tap the mic once to go live, and it stays armed
// across turns -- listening while idle, listening again the instant Bookly
// finishes talking, and (see "barge-in" below) even listening *while*
// Bookly is talking so you can interrupt it mid-reply, the way you would on
// a phone call. Tap again to mute/unmute without ending the session;
// switching back to Chat mode ends it. Speech input uses the browser's free
// SpeechRecognition; speech output tries ElevenLabs first (see speak()),
// falling back to the browser's built-in speechSynthesis on any failure.
//
// The passcode gate below is a lightweight guard for a publicly hosted demo
// link (see BOOKLY_ACCESS_CODE in the backend) -- not real auth. It's a
// documented scope decision, not a security feature: it just keeps random
// crawlers/bots off a URL that calls a real, billed LLM API.

let sessionId = null;
let accessCode = sessionStorage.getItem("bookly_access_code") || "";
let appShown = false;
let mode = "chat"; // "chat" or "voice" -- mirrors the Claude/ChatGPT app mode switch

// Voice-session state. "Live" persists across turns once started; "muted"
// pauses listening without ending the session; "recognizing" / "botSpeaking"
// track what's happening right now so the UI and the barge-in logic below
// can react correctly. waitingForReply exists specifically to avoid a race:
// without it, the mic could pick up a second utterance while /chat is still
// in flight for the first one.
let voiceLive = false;
let voiceMuted = false;
let recognizing = false;
let botSpeaking = false;
let waitingForReply = false;
let currentAudio = null;
let speakingStartedAt = 0;
let currentSpeechText = ""; // normalized text of what Bookly is currently saying -- see isLikelyEcho()

// Barge-in tuning. Plain voice-activity detection (any sound at all) turned
// out to fire on background noise and on Bookly's own voice bleeding back
// into the mic from the speakers -- browsers don't guarantee echo
// cancellation between mic input and speaker output. Requiring an actual
// recognized word or two, and ignoring the first moment of playback (while
// echo is most likely), cuts out most false interrupts, but on a laptop
// without headphones the mic can still pick up real, multi-word fragments
// of Bookly's OWN reply clearly enough to pass both of those checks -- that
// showed up as "audio stopping midway" even mid-sentence. isLikelyEcho()
// below is the actual fix for that: it recognizes when what the mic heard
// is just Bookly's own reply playing back into it, rather than the customer
// talking, by checking it against the text that's actually being spoken
// right now.
const BARGE_IN_MIN_CHARS = 3;
const BARGE_IN_GRACE_MS = 700;

function normalizeForEchoCheck(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// True when `heard` (what the mic just recognized) looks like it's actually
// Bookly's own voice bleeding back in, rather than the customer talking over
// it: either it's a straight substring of the reply being spoken (the common
// case -- the mic catches a clean fragment of it), or most of its words
// appear in the reply in order (looser match, for when recognition garbles a
// word or two of its own echo). A real interruption is essentially never
// going to satisfy either -- it's a different sentence, not a fragment of
// this one.
function isLikelyEcho(heard) {
  if (!currentSpeechText) return false;
  const heardNorm = normalizeForEchoCheck(heard);
  if (!heardNorm) return false;
  if (currentSpeechText.includes(heardNorm)) return true;

  const heardWords = heardNorm.split(" ").filter(Boolean);
  if (heardWords.length < 2) return false; // too short to judge by word overlap alone
  const matched = heardWords.filter((w) => currentSpeechText.includes(w)).length;
  return matched / heardWords.length >= 0.8;
}

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
  if (!isVoice) {
    // Leaving Voice mode ends the live session outright -- coming back
    // starts fresh with a tap, rather than silently listening in the
    // background while you're looking at the text UI.
    endVoiceSession();
  } else {
    updateVoiceUI();
  }
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
  const submitBtn = gateForm.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  submitBtn.textContent = "Verifying...";
  const ok = await tryEnterWithCode(code);
  if (ok) {
    accessCode = code;
    sessionStorage.setItem("bookly_access_code", code);
    showApp();
  } else {
    gateError.textContent = "That code didn't work. Try again.";
    submitBtn.disabled = false;
    submitBtn.textContent = "Enter";
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
  const isVoice = mode === "voice";
  addMessage("user", text);
  inputEl.value = "";
  const typingEl = showTyping();
  waitingForReply = true;
  updateVoiceUI();

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Access-Code": accessCode,
      },
      body: JSON.stringify({ message: text, session_id: sessionId, voice: isVoice }),
    });
    typingEl.remove();
    waitingForReply = false;
    if (!res.ok) {
      const body = await res.text();
      addMessage("system", `Error: ${res.status} ${body}`);
      if (isVoice) armMic(); // still live -- don't strand the session on an error
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
    updateSessionLabel();
    addMessage("assistant", data.reply);
    if (isVoice && voiceLive && !voiceMuted) {
      speak(data.reply); // arms the mic itself, for barge-in during playback
    }
  } catch (err) {
    typingEl.remove();
    waitingForReply = false;
    addMessage("system", `Network error: ${err.message}`);
    if (isVoice) armMic();
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(inputEl.value);
});

// --- Voice input (STT) + persistent voice session ---
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

function updateVoiceUI() {
  if (!SpeechRecognitionCtor) return; // disabled path below owns the UI text
  micBtn.classList.remove("listening", "speaking", "muted");
  if (!voiceLive) {
    voiceStatusEl.textContent = "Tap to start";
    micBtn.title = "Tap to start a live voice conversation";
    return;
  }
  if (voiceMuted) {
    micBtn.classList.add("muted");
    voiceStatusEl.textContent = "Muted — tap to unmute";
    micBtn.title = "Unmute";
    return;
  }
  if (botSpeaking) {
    micBtn.classList.add("speaking");
    voiceStatusEl.textContent = "Speaking — jump in anytime";
    micBtn.title = "Tap to mute";
    return;
  }
  if (recognizing) {
    micBtn.classList.add("listening");
    voiceStatusEl.textContent = "Listening...";
    micBtn.title = "Tap to mute";
    return;
  }
  voiceStatusEl.textContent = waitingForReply ? "Thinking..." : "Listening...";
  micBtn.title = "Tap to mute";
}

// Starts listening if the session is live, unmuted, and not already
// listening. Safe to call opportunistically from several places (after a
// reply, after playback ends, after an error) -- it's a no-op otherwise.
function armMic() {
  if (!recognizer || !voiceLive || voiceMuted || recognizing) return;
  try {
    recognizer.start();
  } catch (err) {
    // Already starting/started, or a transient browser hiccup -- ignore.
  }
}

// Stops whatever's currently being spoken -- used both for the mute button
// and for barge-in (see recognizer.onspeechstart below).
function stopSpeaking() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  botSpeaking = false;
  currentSpeechText = "";
}

function endVoiceSession() {
  voiceLive = false;
  voiceMuted = false;
  stopSpeaking();
  if (recognizer && recognizing) {
    try { recognizer.abort(); } catch (err) { /* ignore */ }
  }
  updateVoiceUI();
}

if (SpeechRecognitionCtor) {
  recognizer = new SpeechRecognitionCtor();
  recognizer.lang = "en-US";
  recognizer.interimResults = true; // needed so barge-in can see real words as they arrive, not just "some sound happened"
  recognizer.maxAlternatives = 1;

  recognizer.onstart = () => {
    recognizing = true;
    updateVoiceUI();
  };

  recognizer.onresult = (event) => {
    let finalTranscript = "";
    let liveTranscript = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalTranscript += chunk;
      else liveTranscript += chunk;
    }

    // Barge-in: only once real recognized words have come through, only
    // after the grace window, and only if it doesn't look like Bookly's own
    // voice echoing back into the mic -- see BARGE_IN_* and isLikelyEcho()
    // above for why each check exists.
    const heardSoFar = (finalTranscript || liveTranscript).trim();
    if (
      botSpeaking &&
      heardSoFar.length >= BARGE_IN_MIN_CHARS &&
      Date.now() - speakingStartedAt > BARGE_IN_GRACE_MS &&
      !isLikelyEcho(heardSoFar)
    ) {
      stopSpeaking();
      updateVoiceUI();
    }

    if (finalTranscript.trim()) {
      inputEl.value = finalTranscript.trim();
      sendMessage(finalTranscript.trim());
    }
  };

  recognizer.onerror = (event) => {
    recognizing = false;
    // Permission/hardware problems shouldn't loop-retry forever and spam the
    // user with repeated browser prompts -- end the session cleanly instead.
    if (["not-allowed", "service-not-allowed", "audio-capture"].includes(event.error)) {
      voiceLive = false;
      voiceMuted = false;
      voiceStatusEl.textContent = "Microphone access blocked — check your browser's site permissions";
      updateVoiceUI();
    }
    // Other errors (no-speech timeout, transient network blips, etc) just
    // fall through to onend, which re-arms the mic if the session is still
    // live -- that's what keeps voice mode listening indefinitely instead
    // of going quiet after one silence timeout.
  };

  recognizer.onend = () => {
    recognizing = false;
    if (voiceLive && !voiceMuted && !waitingForReply && !botSpeaking) {
      armMic();
    } else {
      updateVoiceUI();
    }
  };

  micBtn.addEventListener("click", () => {
    if (!voiceLive) {
      voiceLive = true;
      voiceMuted = false;
      armMic();
      updateVoiceUI();
      return;
    }
    // Already live: this tap toggles mute rather than ending the session --
    // "remain active with option to mute," not "turn off every time."
    voiceMuted = !voiceMuted;
    if (voiceMuted) {
      stopSpeaking();
      if (recognizing) {
        try { recognizer.abort(); } catch (err) { /* ignore */ }
      }
    } else {
      armMic();
    }
    updateVoiceUI();
  });
} else {
  micBtn.disabled = true;
  micBtn.title = "Speech recognition isn't supported in this browser (try Chrome)";
  voiceStatusEl.textContent = "Voice input isn't supported in this browser (try Chrome)";
}

// --- Voice output (TTS) ---
//
// Tries ElevenLabs first (server-side call, real voice quality) and falls
// back to the browser's free built-in speechSynthesis if ElevenLabs isn't
// configured, the free-tier quota is exhausted, or the request fails for
// any other reason. The chat should never break because of a voice issue.
//
// Both functions arm the mic *before* playback finishes (speak() arms it as
// soon as it knows audio is about to play, not after) so a customer talking
// over Bookly is heard immediately rather than after the reply finishes --
// see recognizer.onspeechstart above for the other half of that.

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
  if (!("speechSynthesis" in window)) {
    armMic();
    return;
  }
  window.speechSynthesis.cancel(); // don't stack overlapping utterances
  const spoken = stripForSpeech(text);
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.rate = 1.0;
  utterance.onend = () => {
    botSpeaking = false;
    currentSpeechText = "";
    armMic();
    updateVoiceUI();
  };
  utterance.onerror = () => {
    botSpeaking = false;
    currentSpeechText = "";
    armMic();
    updateVoiceUI();
  };
  botSpeaking = true;
  speakingStartedAt = Date.now();
  currentSpeechText = normalizeForEchoCheck(spoken);
  updateVoiceUI();
  armMic(); // listen while speaking, so a barge-in can interrupt it
  window.speechSynthesis.speak(utterance);
}

async function speak(text) {
  const clean = stripForSpeech(text);
  if (!clean) {
    armMic();
    return;
  }
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
      currentAudio = audio;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        if (currentAudio === audio) currentAudio = null;
        botSpeaking = false;
        currentSpeechText = "";
        armMic();
        updateVoiceUI();
      };
      audio.onerror = () => {
        if (currentAudio === audio) currentAudio = null;
        botSpeaking = false;
        currentSpeechText = "";
        armMic();
        updateVoiceUI();
      };
      botSpeaking = true;
      speakingStartedAt = Date.now();
      currentSpeechText = normalizeForEchoCheck(clean);
      updateVoiceUI();
      armMic(); // listen while speaking, so a barge-in can interrupt it
      await audio.play();
      return;
    }
    // 204 = ElevenLabs not configured, or another non-fatal issue -- fall through.
  } catch (err) {
    // network error, etc -- fall through to browser TTS.
  }
  speakWithBrowser(text);
}
