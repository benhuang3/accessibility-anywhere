document.getElementById("add").addEventListener("click", () => {
  const c = document.getElementById("count");
  c.textContent = String(Number(c.textContent) + 1);
});
document.getElementById("menu").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("menu-state").textContent = "open";
});
document.getElementById("save").addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  document.getElementById("saved").textContent = "saved";
});
document.getElementById("trash").addEventListener("click", () => {
  document.getElementById("trash-state").textContent = "deleted";
});
document.getElementById("buy").addEventListener("click", () => { document.title = "Bought"; });
