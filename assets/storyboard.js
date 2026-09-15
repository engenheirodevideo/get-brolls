(() => {
  const templates = [...document.querySelectorAll("template[data-shot]")];
  const select = document.getElementById("select"),
    viewer = document.getElementById("viewer");
  if (!templates.length) return;
  let index = 0,
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
        button.querySelector("span").textContent = playing
          ? "▶ Assistir trecho"
          : "■ Parar GIF";
      }),
    );
  }
  function render() {
    stop();
    const t = templates[index];
    viewer.replaceChildren(t.content.cloneNode(true));
    viewer.querySelector(".presenter").hidden = mode === "material";
    viewer.classList.toggle("single", mode === "material");
    const caption = viewer.querySelector(".caption-content");
    if (
      document.body.classList.contains("film-board") ||
      viewer.querySelector(".review-panel")
    )
      viewer
        .querySelector(".material")
        .insertBefore(caption, viewer.querySelector(".review-panel"));
    else document.getElementById("caption").replaceChildren(caption);
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches)
      viewer.querySelectorAll("[data-gif]").forEach((button) => {
        const img = button.querySelector("img");
        img.loading = "eager";
        img.src = button.dataset.gif;
        button.setAttribute("aria-pressed", "true");
        button.querySelector("span").textContent = "■ Parar GIF";
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
  select.addEventListener("change", () => {
    index = Number(select.value);
    render();
  });
  document.getElementById("prev").onclick = () => {
    if (index > 0) {
      index--;
      render();
    }
  };
  document.getElementById("next").onclick = () => {
    if (index < templates.length - 1) {
      index++;
      render();
    }
  };
  document.querySelectorAll("[data-index]").forEach(
    (b) =>
      (b.onclick = () => {
        index = Number(b.dataset.index);
        render();
        document.getElementById("tools").scrollIntoView({ block: "start" });
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
    if (e.key === "ArrowRight" && index < templates.length - 1) {
      e.preventDefault();
      index++;
      render();
    } else if (e.key === "ArrowLeft" && index > 0) {
      e.preventDefault();
      index--;
      render();
    }
  });
  window.addEventListener("pagehide", stop);
  render();
})();
