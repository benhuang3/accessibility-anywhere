// Likes and the chat box only change this page. Nothing is saved or sent anywhere.
for (const button of document.querySelectorAll("[data-like]")) {
  button.addEventListener("click", () => {
    const on = button.classList.toggle("liked");
    button.textContent = on ? "Liked" : "Like";
  });
}
const form = document.getElementById("chat-form");
if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = form.elements.message;
    const text = input.value.trim();
    if (!text) return;
    const bubble = document.createElement("div");
    bubble.className = "bubble me";
    bubble.textContent = text;
    document.getElementById("chat-log").append(bubble);
    input.value = "";
  });
}
const bell = document.getElementById("bell");
if (bell) {
  bell.addEventListener("click", () => {
    const panel = document.getElementById("notifications");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    panel.focus({ preventScroll: true });
  });
}
