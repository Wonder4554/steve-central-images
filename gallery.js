/* Data-driven photo gallery (static grid, no animation).
   Any <div class="auto-gallery"></div> is filled from the /api/gallery
   endpoint with a responsive grid of photos. Falls back to a built-in list
   when the API is unavailable (e.g. the page is opened as a plain file). */
(function () {
  function render(container, images) {
    if (!images || !images.length) {
      container.style.display = "none";
      return;
    }
    container.innerHTML = "";
    var grid = document.createElement("div");
    grid.className = "gallery-grid";
    images.forEach(function (name) {
      var img = document.createElement("img");
      img.src = "images/" + name;
      img.alt = name.replace(/\.[^.]+$/, "");
      img.loading = "lazy";
      grid.appendChild(img);
    });
    container.appendChild(grid);
  }

  // Built-in photo list, used when the server isn't running (page opened
  // as a plain file). Layout assets (logo, background, Price) are excluded.
  var FALLBACK_IMAGES = [
    "Beautiful.jpg", "Clean.jpg", "Color.jpg", "Concert.jpg", "Creative.jpg",
    "Family.jpg", "Girl.jpg", "Girls.jpg", "Iceland.jpg", "Nature.jpg",
    "Nice.jpg", "Orientation.jpg", "Repas.jpg", "School.jpg", "Wedding.jpg", "Work.jpg"
  ];

  document.addEventListener("DOMContentLoaded", function () {
    var containers = document.querySelectorAll(".auto-gallery");
    if (!containers.length) return;
    fetch("/api/gallery")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var imgs = (data.images && data.images.length) ? data.images : FALLBACK_IMAGES;
        containers.forEach(function (c) { render(c, imgs); });
      })
      .catch(function () {
        containers.forEach(function (c) { render(c, FALLBACK_IMAGES); });
      });
  });
})();
