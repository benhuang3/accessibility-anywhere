const box = document.getElementById("mail");
for (let i = 1; i <= 100; i++) {
  const p = document.createElement("p");
  p.textContent = `Message ${i}: hello from a friend.`;
  box.appendChild(p);
}
