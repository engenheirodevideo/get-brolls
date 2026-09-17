(() => {
  const templates = [...document.querySelectorAll("template[data-shot]")];
  const select = document.getElementById("select"),
    viewer = document.getElementById("viewer");
  if (!templates.length) return;
  // Open on the first frame that has a real preview (issue #16, finding 3).
  const firstPreview = templates.findIndex((t) => t.dataset.preview === "1");
  let index = firstPreview >= 0 ? firstPreview : 0,
    mode = "side";
  function stop() {}
  function wire() {
    viewer.querySelectorAll("[data-gif]").forEach((button) =>
      button.addEventListener("click", () => {
        const playing = button.getAttribute("aria-pressed") === "true";
        button.querySelector("img").src = playing
          ? button.dataset.poster
          : button.dataset.gif;
        button.setAttribute("aria-pressed", String(!playing));
        const label = button.querySelector("span");
        if (label) label.textContent = playing ? "▶ Assistir trecho" : "■ Parar GIF";
      }),
    );
  }
  function render() {
    stop();
    const t = templates[index];
    viewer.replaceChildren(t.content.cloneNode(true));
    viewer.querySelector(".presenter").hidden = mode === "material";
    viewer.classList.toggle("single", mode === "material");
    const caption = viewer.querySelector(":scope > .caption-content");
    const captionBox = document.getElementById("caption");
    if (caption) {
      const material = viewer.querySelector(".material");
      if (document.body.classList.contains("film-board") || viewer.querySelector(".review-panel"))
        material.insertBefore(caption, viewer.querySelector(".review-panel"));
      else captionBox.replaceChildren(caption);
    } else captionBox.replaceChildren();
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches)
      viewer.querySelectorAll("[data-gif]").forEach((button) => {
        const img = button.querySelector("img");
        img.loading = "eager";
        img.src = button.dataset.gif;
        button.setAttribute("aria-pressed", "true");
        const label = button.querySelector("span");
        if (label) label.textContent = "■ Parar GIF";
      });
    select.value = String(index);
    document.getElementById("prev").disabled = index === 0;
    document.getElementById("next").disabled = index === templates.length - 1;
    document.getElementById("position").textContent =
      `${String(index + 1).padStart(2, "0")} / ${String(templates.length).padStart(2, "0")}`;
    document
      .querySelectorAll("[data-index]")
      .forEach((b) =>
        b.setAttribute(
          "aria-current",
          String(Number(b.dataset.index) === index),
        ),
      );
    wire();
  }
  function go(next) {
    index = Math.max(0, Math.min(templates.length - 1, next));
    render();
  }
  window.getbrollsGo = go;
  select.addEventListener("change", () => go(Number(select.value)));
  document.getElementById("prev").onclick = () => go(index - 1);
  document.getElementById("next").onclick = () => go(index + 1);
  document.querySelectorAll("[data-index]").forEach(
    (b) =>
      (b.onclick = () => {
        go(Number(b.dataset.index));
        document.getElementById("viewer").scrollIntoView({ block: "start" });
        select.focus({ preventScroll: true });
      }),
  );
  document.querySelectorAll("[data-mode]").forEach(
    (b) =>
      (b.onclick = () => {
        mode = b.dataset.mode;
        document
          .querySelectorAll("[data-mode]")
          .forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        render();
      }),
  );
  document.addEventListener("keydown", (e) => {
    if (
      /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) ||
      e.altKey ||
      e.ctrlKey ||
      e.metaKey
    )
      return;
    if (e.key === "ArrowRight") {
      e.preventDefault();
      go(index + 1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      go(index - 1);
    }
  });
  window.addEventListener("pagehide", stop);
  render();
})();
