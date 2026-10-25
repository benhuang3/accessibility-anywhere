for (const form of document.querySelectorAll("[data-demo-form]")) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = form.dataset.demoForm === "shop"
      ? "Demo reservation complete. Nothing was saved, sent, or charged."
      : "Demo only. No verification took place; nothing was saved or sent.";
    form.reset();
    form.querySelector("[role=status]").textContent = message;
  });
}
