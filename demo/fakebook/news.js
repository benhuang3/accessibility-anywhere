// "Show image" reveals the picture and plays a short, soft rising chime made with Web Audio (no audio files).
const button = document.getElementById("show-image");
const photo = document.getElementById("photo");

function chime() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  const ctx = new Ctx();
  const start = ctx.currentTime + 0.02;
  [523.25, 659.25, 783.99, 1046.5].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const t = start + i * 0.14;
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.18, t + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.55);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.6);
  });
  setTimeout(() => ctx.close(), 1500);
}

if (button && photo) {
  button.addEventListener("click", () => {
    photo.hidden = false;
    button.hidden = true;
    try { chime(); } catch (error) { /* sound is optional */ }
  });
}
