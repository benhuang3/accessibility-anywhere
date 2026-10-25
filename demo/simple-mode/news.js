// The "Show image" button under the story: shows (or hides) the photo of Biscuit and Pepper.
const button = document.getElementById("show-photo");
const photo = document.getElementById("photo");
button.addEventListener("click", () => {
  photo.hidden = !photo.hidden;
  button.textContent = photo.hidden ? "Show image" : "Hide image";
  button.setAttribute("aria-expanded", String(!photo.hidden));
});
