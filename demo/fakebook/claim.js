// Fake urgency only: a countdown, a falling "gifts left" number and in-page toasts. No alert(), no downloads,
// no fullscreen, no history tricks. The page closes and navigates away normally, and the form sends nothing.
const timer = document.getElementById("timer");
const left = document.getElementById("left");
const toasts = document.getElementById("toasts");
let seconds = 299;

setInterval(() => {
  seconds = seconds > 0 ? seconds - 1 : 299;
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  timer.textContent = `${m}:${s}`;
}, 1000);

const messages = [
  ["You've been selected!", ""],
  ["Claim now! Only 3 left!", ""],
  ["Your account will be suspended", ""],
  ["1 person is claiming this prize right now", "green"],
  ["Offer expires in 04:59", ""],
  ["Congratulations winner! Don't wait", "green"],
];
const MAX_TOASTS = 4;

function toast([text, tone]) {
  const el = document.createElement("div");
  el.className = "toast" + (tone ? " " + tone : "");
  el.textContent = text;
  const close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.textContent = "×";
  close.addEventListener("click", () => el.remove());
  el.append(close);
  toasts.prepend(el);
  while (toasts.children.length > MAX_TOASTS) toasts.lastChild.remove();
}

messages.forEach((message, i) => setTimeout(() => {
  toast(message);
  if (i === 2) left.textContent = "2";
  if (i === 4) left.textContent = "1";
}, 600 + i * 900));
setInterval(() => toast(messages[Math.floor(Math.random() * messages.length)]), 4000);

document.getElementById("claim-form").addEventListener("submit", (event) => {
  event.preventDefault();
  document.getElementById("status").textContent = "Demo only: nothing was sent or saved.";
});
