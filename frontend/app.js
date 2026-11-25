const doctorsListEl = document.getElementById("doctorsList");
const appointmentsBodyEl = document.getElementById("appointmentsBody");
const dashboardTranscriptEl = document.getElementById("dashboardTranscript");
const sseStatusEl = document.getElementById("sseStatus");
const sseStatusTextEl = document.getElementById("sseStatusText");

const kpiDoctorsEl = document.getElementById("kpiDoctors");
const kpiSlotsEl = document.getElementById("kpiSlots");
const kpiBookingsEl = document.getElementById("kpiBookings");
const appointmentsMetaEl = document.getElementById("appointmentsMeta");

const tryCallBtn = document.getElementById("tryCallBtn");
const clearTranscriptBtn = document.getElementById("clearTranscriptBtn");

// Phone modal elements
const phoneModalEl = document.getElementById("phoneModal");
const phoneCallStateEl = document.getElementById("phoneCallState");
const phoneCallTimerEl = document.getElementById("phoneCallTimer");
const phoneAvatarRingEl = document.getElementById("phoneAvatarRing");
const phoneConfirmCardEl = document.getElementById("phoneConfirmCard");
const phoneTranscriptEl = document.getElementById("phoneTranscript");
const muteBtn = document.getElementById("muteBtn");
const muteLabelEl = document.getElementById("muteLabel");
const endCallBtn = document.getElementById("endCallBtn");
const remoteAudioEl = document.getElementById("remoteAudio");
const waveformCanvas = document.getElementById("waveformCanvas");
const waveCtx = waveformCanvas.getContext("2d");

let pc = null;
let localStream = null;
let audioCtx = null;
let analyser = null;
let animFrameId = null;
let timerInterval = null;
let callSeconds = 0;
let isMuted = false;
let ringInterval = null;
let lastHighlightId = null;
let lastHighlightType = null;

// --- Data Fetching & Rendering ---
async function refreshDashboard() {
  const [docRes, aptRes] = await Promise.all([
    fetch("/api/doctors"),
    fetch("/api/appointments"),
  ]);
  const { doctors } = await docRes.json();
  const { appointments } = await aptRes.json();

  renderDoctors(doctors);
  renderAppointments(appointments);
  updateKpis(doctors, appointments);
}

function updateKpis(doctors, appointments) {
  kpiDoctorsEl.textContent = doctors.length;
  const openSlots = doctors.reduce(
    (sum, doc) => sum + doc.slots.filter((s) => !s.is_booked).length,
    0
  );
  kpiSlotsEl.textContent = openSlots;
  const activeBookings = appointments.filter((a) => a.status === "confirmed").length;
  kpiBookingsEl.textContent = activeBookings;
  appointmentsMetaEl.textContent = `${appointments.length} records`;
}

function renderDoctors(doctors) {
  doctorsListEl.innerHTML = doctors
    .map(
      (doc) => `
      <div class="doctor-card">
        <div class="doctor-top">
          <div>
            <div class="doctor-name">${escapeHtml(doc.name)}</div>
            <div class="doctor-room">${escapeHtml(doc.room)}</div>
          </div>
          <span class="specialty-badge">${escapeHtml(doc.specialty)}</span>
        </div>
        <div class="slots-wrap">
          ${doc.slots
            .map(
              (s) => `
            <span class="slot-chip ${s.is_booked ? "booked" : ""}">
              ${escapeHtml(s.date.slice(5))} · ${escapeHtml(s.time)}
            </span>`
            )
            .join("")}
        </div>
      </div>`
    )
    .join("");
}

function renderAppointments(appointments) {
  appointmentsBodyEl.innerHTML = appointments
    .map((apt) => {
      let flashClass = "";
      if (apt.id === lastHighlightId) {
        flashClass =
          lastHighlightType === "booked" ? "row-flash-booked" : "row-flash-cancelled";
      }
      return `
      <tr class="${flashClass}">
        <td class="mono">${escapeHtml(apt.id)}</td>
        <td class="mono">${escapeHtml(apt.phone_number)}</td>
        <td>
          <strong>${escapeHtml(apt.doctor_name)}</strong><br/>
          <span style="color:#64748b;font-size:12px;">${escapeHtml(apt.specialty)}</span>
        </td>
        <td class="mono">${escapeHtml(apt.date)} · ${escapeHtml(apt.time)}</td>
        <td><span class="status-tag ${escapeHtml(apt.status)}">${escapeHtml(apt.status)}</span></td>
      </tr>`;
    })
    .join("");
}

function appendTranscript(speaker, text) {
  const emptyDash = dashboardTranscriptEl.querySelector(".empty-state");
  if (emptyDash) emptyDash.remove();

  const item = document.createElement("div");
  item.className = `msg-item ${speaker}`;
  item.innerHTML = `
    <span class="msg-meta">${speaker === "user" ? "Caller" : "Careline AI"} · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
    <span>${escapeHtml(text)}</span>
  `;
  dashboardTranscriptEl.appendChild(item);
  dashboardTranscriptEl.scrollTop = dashboardTranscriptEl.scrollHeight;

  const emptyPhone = phoneTranscriptEl.querySelector(".phone-transcript-empty");
  if (emptyPhone) emptyPhone.remove();

  const pItem = document.createElement("div");
  pItem.className = `phone-msg ${speaker}`;
  pItem.textContent = text;
  phoneTranscriptEl.appendChild(pItem);
  phoneTranscriptEl.scrollTop = phoneTranscriptEl.scrollHeight;
}

function showPhoneConfirmation(type, data) {
  phoneConfirmCardEl.classList.remove("hidden", "cancelled");
  if (type === "cancelled") {
    phoneConfirmCardEl.classList.add("cancelled");
    phoneConfirmCardEl.innerHTML = `
      <strong>Appointment Cancelled · ${escapeHtml(data.id)}</strong>
      <span>${escapeHtml(data.doctor_name)} · ${escapeHtml(data.date)} at ${escapeHtml(data.time)}</span>
    `;
  } else {
    phoneConfirmCardEl.innerHTML = `
      <strong>Appointment Confirmed · ${escapeHtml(data.id)}</strong>
      <span>${escapeHtml(data.doctor_name)} (${escapeHtml(data.room)})</span>
      <span>${escapeHtml(data.date)} at ${escapeHtml(data.time)} · ${escapeHtml(data.phone_number)}</span>
    `;
  }
}

// --- SSE Real-Time Sync ---
function connectSSE() {
  const evtSource = new EventSource("/api/events");
  evtSource.onopen = () => {
    sseStatusEl.classList.add("live");
    sseStatusTextEl.textContent = "Live";
  };
  evtSource.onerror = () => {
    sseStatusEl.classList.remove("live");
    sseStatusTextEl.textContent = "Reconnecting";
  };
  evtSource.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "transcript") {
      appendTranscript(msg.data.speaker, msg.data.text);
    } else if (msg.type === "appointment_booked") {
      lastHighlightId = msg.data.id;
      lastHighlightType = "booked";
      showPhoneConfirmation("booked", msg.data);
      refreshDashboard();
    } else if (msg.type === "appointment_cancelled") {
      lastHighlightId = msg.data.id;
      lastHighlightType = "cancelled";
      showPhoneConfirmation("cancelled", msg.data);
      refreshDashboard();
    } else if (msg.type === "call_state") {
      if (msg.data.state === "connected") {
        setCallConnectedUI();
      } else if (msg.data.state === "ended") {
        endCall();
      }
    } else if (msg.type === "call_error") {
      phoneCallStateEl.textContent = msg.data.message || "Call error";
    }
  };
}

// --- Synthesized Call Tones (Web Audio API) ---
function startRingTone() {
  stopRingTone();
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const playBeep = () => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 440;
    gain.gain.setValueAtTime(0.04, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.45);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.45);
  };
  playBeep();
  ringInterval = setInterval(playBeep, 1800);
  ringInterval._ctx = ctx;
}

function stopRingTone() {
  if (ringInterval) {
    clearInterval(ringInterval);
    if (ringInterval._ctx) ringInterval._ctx.close().catch(() => {});
    ringInterval = null;
  }
}

function playEndTone() {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(520, ctx.currentTime);
  osc.frequency.exponentialRampToValueAtTime(320, ctx.currentTime + 0.25);
  gain.gain.setValueAtTime(0.05, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.28);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.3);
  setTimeout(() => ctx.close().catch(() => {}), 400);
}

// --- Waveform Visualizer ---
function startWaveform(stream) {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 64;
  const source = audioCtx.createMediaStreamSource(stream);
  source.connect(analyser);

  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);

  function draw() {
    animFrameId = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(dataArray);
    waveCtx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);

    const barCount = 28;
    const step = Math.floor(bufferLength / barCount) || 1;
    const barWidth = 5;
    const gap = (waveformCanvas.width - barCount * barWidth) / (barCount + 1);

    for (let i = 0; i < barCount; i++) {
      const val = dataArray[i * step] || 0;
      const barH = Math.max(4, (val / 255) * (waveformCanvas.height - 10));
      const x = gap + i * (barWidth + gap);
      const y = (waveformCanvas.height - barH) / 2;
      waveCtx.fillStyle = "#5eead4";
      waveCtx.beginPath();
      waveCtx.roundRect(x, y, barWidth, barH, 3);
      waveCtx.fill();
    }
  }
  draw();
}

function stopWaveform() {
  if (animFrameId) cancelAnimationFrame(animFrameId);
  animFrameId = null;
  if (audioCtx) {
    audioCtx.close().catch(() => {});
    audioCtx = null;
  }
  waveCtx.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
}

// --- WebRTC Call Lifecycle ---
async function startCall() {
  phoneModalEl.classList.remove("hidden");
  phoneConfirmCardEl.classList.add("hidden");
  phoneTranscriptEl.innerHTML = `<div class="phone-transcript-empty">Dialing clinic assistant...</div>`;
  phoneCallStateEl.textContent = "Dialing...";
  phoneCallTimerEl.textContent = "00:00";
  muteBtn.disabled = true;
  isMuted = false;
  muteBtn.classList.remove("muted");
  muteLabelEl.textContent = "Mute";

  startRingTone();

  try {
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    startWaveform(localStream);

    pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });

    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

    pc.ontrack = (event) => {
      remoteAudioEl.srcObject = event.streams[0];
    };

    pc.oniceconnectionstatechange = () => {
      if (pc && (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed")) {
        setCallConnectedUI();
      }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const res = await fetch("/api/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
      }),
    });

    if (!res.ok) throw new Error("WebRTC signaling failed");
    const answer = await res.json();
    await pc.setRemoteDescription(new RTCSessionDescription(answer));
  } catch (err) {
    stopRingTone();
    phoneCallStateEl.textContent = "Connection failed";
    console.error(err);
  }
}

function setCallConnectedUI() {
  stopRingTone();
  if (timerInterval) return;
  phoneCallStateEl.textContent = "Connected";
  phoneAvatarRingEl.classList.add("pulse");
  muteBtn.disabled = false;

  callSeconds = 0;
  timerInterval = setInterval(() => {
    callSeconds += 1;
    const mins = String(Math.floor(callSeconds / 60)).padStart(2, "0");
    const secs = String(callSeconds % 60).padStart(2, "0");
    phoneCallTimerEl.textContent = `${mins}:${secs}`;
  }, 1000);
}

function endCall() {
  stopRingTone();
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
  phoneAvatarRingEl.classList.remove("pulse");

  if (pc) {
    pc.close();
    pc = null;
  }
  if (localStream) {
    localStream.getTracks().forEach((t) => t.stop());
    localStream = null;
  }
  stopWaveform();
  playEndTone();
  phoneCallStateEl.textContent = "Call Ended";
  muteBtn.disabled = true;

  setTimeout(() => {
    phoneModalEl.classList.add("hidden");
  }, 900);
}

muteBtn.addEventListener("click", () => {
  if (!localStream) return;
  isMuted = !isMuted;
  localStream.getAudioTracks().forEach((t) => {
    t.enabled = !isMuted;
  });
  muteBtn.classList.toggle("muted", isMuted);
  muteLabelEl.textContent = isMuted ? "Unmute" : "Mute";
});

tryCallBtn.addEventListener("click", startCall);
endCallBtn.addEventListener("click", endCall);
clearTranscriptBtn.addEventListener("click", () => {
  dashboardTranscriptEl.innerHTML = `<div class="empty-state">Start a call to view the live conversation.</div>`;
});

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

refreshDashboard();
connectSSE();
