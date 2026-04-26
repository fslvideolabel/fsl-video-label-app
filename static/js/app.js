const liveVideo = document.getElementById("liveVideo");
const recordedVideo = document.getElementById("recordedVideo");
const startCameraBtn = document.getElementById("startCameraBtn");
const captureBtn = document.getElementById("captureBtn");
const captureBtnText = document.getElementById("captureBtnText");
const sendBtn = document.getElementById("sendBtn");
const secondsSelect = document.getElementById("seconds");
const resultBox = document.getElementById("resultBox");
const statusEl = document.getElementById("status");
const timerEl = document.getElementById("timer");
const resultsCard = document.getElementById("resultsCard");
const previewWrap = document.getElementById("previewWrap");
const recordingBadge = document.getElementById("recordingBadge");
const emptyHint = document.getElementById("emptyHint");
const analyzeModal = document.getElementById("analyzeModal");

const playPauseBtn = document.getElementById("playPauseBtn");
const restartBtn = document.getElementById("restartBtn");
const muteBtn = document.getElementById("muteBtn");
const fullscreenBtn = document.getElementById("fullscreenBtn");
const seekBar = document.getElementById("seekBar");
const currentTimeEl = document.getElementById("currentTime");
const durationTimeEl = document.getElementById("durationTime");

let stream = null;
let mediaRecorder = null;
let recordedChunks = [];
let recordedBlob = null;
let countdownInterval = null;
let recordTimeout = null;

let isRecording = false;
let isAnalyzing = false;
let hasOpenedCamera = false;

const isTranslatePage =
  liveVideo &&
  recordedVideo &&
  startCameraBtn &&
  captureBtn &&
  sendBtn &&
  secondsSelect &&
  resultBox &&
  statusEl &&
  timerEl;

function setStatus(text) {
  if (statusEl) {
    statusEl.textContent = `Status: ${text}`;
  }
}

function showResults() {
  if (resultsCard) {
    resultsCard.style.display = "block";
  }
}

function hideResults() {
  if (resultsCard) {
    resultsCard.style.display = "none";
  }
}

function setResultLabelOnly(labelText) {
  if (!resultBox) return;
  resultBox.textContent = labelText;
  showResults();
}

function setPreviewMode(hasRecording) {
  if (!previewWrap) return;

  if (hasRecording) {
    previewWrap.classList.add("has-recording");
  } else {
    previewWrap.classList.remove("has-recording");
  }
}

function setEmptyHintVisible(visible) {
  if (!emptyHint) return;
  emptyHint.classList.toggle("hidden", !visible);
}

function setRecordingUI(active) {
  isRecording = active;

  if (recordingBadge) {
    recordingBadge.classList.toggle("is-visible", active);
  }

  if (captureBtn) {
    captureBtn.classList.toggle("is-recording", active);
  }

  if (captureBtnText) {
    captureBtnText.textContent = active ? "Stop" : "Record";
  }

  const icon = captureBtn?.querySelector(".material-symbols-outlined");
  if (icon) {
    icon.textContent = active ? "stop" : "fiber_manual_record";
  }

  if (active) {
    startCameraBtn.disabled = true;
    sendBtn.disabled = true;
    secondsSelect.disabled = true;
  } else {
    startCameraBtn.disabled = false;
    secondsSelect.disabled = false;
    sendBtn.disabled = !recordedBlob;
  }
}

function setAnalyzingUI(active) {
  isAnalyzing = active;

  startCameraBtn.disabled = active;
  captureBtn.disabled = active || !hasOpenedCamera;
  sendBtn.disabled = active || !recordedBlob;
  secondsSelect.disabled = active;

  if (analyzeModal) {
    analyzeModal.classList.toggle("hidden", !active);
    analyzeModal.setAttribute("aria-hidden", active ? "false" : "true");
  }
}

function formatTime(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const mins = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

function resetPlaybackUI() {
  if (seekBar) {
    seekBar.value = 0;
  }
  if (currentTimeEl) {
    currentTimeEl.textContent = "0:00";
  }
  if (durationTimeEl) {
    durationTimeEl.textContent = "0:00";
  }

  playPauseBtn.disabled = !recordedBlob;
  restartBtn.disabled = !recordedBlob;
  muteBtn.disabled = !recordedBlob;
  fullscreenBtn.disabled = !recordedBlob;
  seekBar.disabled = !recordedBlob;

  const playIcon = playPauseBtn?.querySelector(".material-symbols-outlined");
  if (playIcon) {
    playIcon.textContent = "play_arrow";
  }

  const muteIcon = muteBtn?.querySelector(".material-symbols-outlined");
  if (muteIcon) {
    muteIcon.textContent = recordedVideo.muted ? "volume_off" : "volume_up";
  }
}

function updatePlaybackProgress() {
  if (!recordedVideo || !recordedBlob) return;

  const duration = recordedVideo.duration || 0;
  const current = recordedVideo.currentTime || 0;

  if (seekBar && duration > 0) {
    seekBar.value = String((current / duration) * 100);
  } else if (seekBar) {
    seekBar.value = "0";
  }

  if (currentTimeEl) {
    currentTimeEl.textContent = formatTime(current);
  }
  if (durationTimeEl) {
    durationTimeEl.textContent = formatTime(duration);
  }
}

function stopCountdown() {
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
  if (recordTimeout) {
    clearTimeout(recordTimeout);
    recordTimeout = null;
  }
  if (timerEl) {
    timerEl.textContent = "";
  }
}

function startCountdown(seconds) {
  let remaining = seconds;
  timerEl.textContent = `Recording: ${remaining}s`;

  countdownInterval = setInterval(() => {
    remaining -= 1;

    if (remaining > 0) {
      timerEl.textContent = `Recording: ${remaining}s`;
    } else {
      timerEl.textContent = "";
      clearInterval(countdownInterval);
      countdownInterval = null;
    }
  }, 1000);
}

function getSupportedMimeType() {
  const candidates = [
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
    "video/mp4"
  ];

  for (const type of candidates) {
    if (MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }

  return "";
}

async function openCamera() {
  if (isRecording || isAnalyzing) return;

  try {
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }

    stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: true
    });

    liveVideo.srcObject = stream;

    hasOpenedCamera = true;
    captureBtn.disabled = false;
    setStatus("camera ready");
    setEmptyHintVisible(false);

    if (!recordedBlob) {
      hideResults();
      setPreviewMode(false);
      sendBtn.disabled = true;
    }
  } catch (err) {
    console.error(err);
    setStatus("camera failed");
    setResultLabelOnly(`Camera error: ${err.message}`);
  }
}

function startRecording() {
  if (!stream || isAnalyzing) {
    setResultLabelOnly("Open the camera first.");
    return;
  }

  const seconds = parseInt(secondsSelect.value, 10);
  const mimeType = getSupportedMimeType();

  recordedChunks = [];
  recordedBlob = null;
  recordedVideo.pause();
  recordedVideo.removeAttribute("src");
  recordedVideo.load();

  hideResults();
  setPreviewMode(false);
  resetPlaybackUI();

  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    };

    mediaRecorder.onstop = () => {
      const finalType = mediaRecorder.mimeType || "video/webm";
      recordedBlob = new Blob(recordedChunks, { type: finalType });
      const url = URL.createObjectURL(recordedBlob);

      recordedVideo.src = url;
      recordedVideo.load();

      setPreviewMode(true);
      setRecordingUI(false);
      stopCountdown();
      setStatus("recording complete");
      resetPlaybackUI();
      sendBtn.disabled = false;
    };

    mediaRecorder.start();
    setRecordingUI(true);
    setStatus("recording...");
    startCountdown(seconds);

    recordTimeout = setTimeout(() => {
      stopRecording();
    }, seconds * 1000);
  } catch (err) {
    console.error(err);
    stopCountdown();
    setRecordingUI(false);
    setStatus("recording failed");
    setResultLabelOnly(`Recording error: ${err.message}`);
  }
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state !== "recording") return;

  stopCountdown();
  mediaRecorder.stop();
}

function toggleRecord() {
  if (isAnalyzing) return;

  if (!isRecording) {
    startRecording();
  } else {
    stopRecording();
  }
}

async function analyzeVideo() {
  if (!recordedBlob || isRecording || isAnalyzing) {
    setResultLabelOnly("No recorded video available.");
    return;
  }

  try {
    setStatus("analyzing...");
    setAnalyzingUI(true);
    hideResults();

    const extension = recordedBlob.type.includes("mp4") ? "mp4" : "webm";
    const file = new File([recordedBlob], `recording.${extension}`, {
      type: recordedBlob.type || "video/webm"
    });

    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/predict", {
      method: "POST",
      body: formData
    });

    const data = await response.json();

    if (response.ok) {
      const label = data?.label || "UNKNOWN";
      setResultLabelOnly(label);
      setStatus("done");
    } else {
      const message = data?.message || "Server error";
      setResultLabelOnly(message);
      setStatus("server error");
    }
  } catch (err) {
    console.error(err);
    setStatus("upload failed");
    setResultLabelOnly(`Analyze error: ${err.message}`);
  } finally {
    setAnalyzingUI(false);
  }
}

function togglePlayback() {
  if (!recordedBlob) return;

  if (recordedVideo.paused) {
    recordedVideo.play();
  } else {
    recordedVideo.pause();
  }
}

function restartPlayback() {
  if (!recordedBlob) return;
  recordedVideo.currentTime = 0;
  recordedVideo.play();
}

function toggleMute() {
  if (!recordedBlob) return;
  recordedVideo.muted = !recordedVideo.muted;

  const muteIcon = muteBtn?.querySelector(".material-symbols-outlined");
  if (muteIcon) {
    muteIcon.textContent = recordedVideo.muted ? "volume_off" : "volume_up";
  }
}

function goFullscreen() {
  if (!previewWrap) return;

  if (document.fullscreenElement) {
    document.exitFullscreen?.();
    return;
  }

  previewWrap.requestFullscreen?.();
}

function onSeek() {
  if (!recordedBlob || !recordedVideo.duration) return;

  const percent = Number(seekBar.value || 0);
  recordedVideo.currentTime = (percent / 100) * recordedVideo.duration;
}

if (isTranslatePage) {
  startCameraBtn.addEventListener("click", openCamera);
  captureBtn.addEventListener("click", toggleRecord);
  sendBtn.addEventListener("click", analyzeVideo);

  playPauseBtn.addEventListener("click", togglePlayback);
  restartBtn.addEventListener("click", restartPlayback);
  muteBtn.addEventListener("click", toggleMute);
  fullscreenBtn.addEventListener("click", goFullscreen);
  seekBar.addEventListener("input", onSeek);

  recordedVideo.addEventListener("loadedmetadata", updatePlaybackProgress);
  recordedVideo.addEventListener("timeupdate", updatePlaybackProgress);
  recordedVideo.addEventListener("play", () => {
    const playIcon = playPauseBtn?.querySelector(".material-symbols-outlined");
    if (playIcon) {
      playIcon.textContent = "pause";
    }
  });

  recordedVideo.addEventListener("pause", () => {
    const playIcon = playPauseBtn?.querySelector(".material-symbols-outlined");
    if (playIcon) {
      playIcon.textContent = "play_arrow";
    }
  });

  recordedVideo.addEventListener("ended", () => {
    const playIcon = playPauseBtn?.querySelector(".material-symbols-outlined");
    if (playIcon) {
      playIcon.textContent = "play_arrow";
    }
  });

  window.addEventListener("beforeunload", () => {
    stopCountdown();

    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }
  });

  resetPlaybackUI();
  setEmptyHintVisible(true);
  hideResults();
}