(() => {
  "use strict";
  const data = window.GETBROLLS_REVIEW;
  if (!data.items.length) return;
  const key = "getbrolls-v2:" + data.project;
  const STATES = ["pending", "approved", "changes", "rejected", "alternative"];
  const LABELS = {
    pending: "Você ainda não disse",
    approved: "Aprovado",
    changes: "Pedi ajuste",
    rejected: "Não serve",
    alternative: "Pedi outro vídeo",
  };
  let saved = {};
  let available = true;
  try {
    saved = JSON.parse(localStorage.getItem(key) || "{}");
    if (!saved || typeof saved !== "object") saved = {};
  } catch {
    available = false;
  }
  const decisions = {};
  for (const item of data.items) {
    const old = saved[item.id];
    const prior =
      old && old.signature === item.signature && old.reviewEpoch === item.reviewEpoch
        ? old
        : item.review;
    decisions[item.id] = {
      signature: item.signature,
      reviewEpoch: item.reviewEpoch,
      state: STATES.includes(prior?.state) ? prior.state : "pending",
      comment: typeof prior?.comment === "string" ? prior.comment : "",
      suggestion: typeof prior?.suggestion === "string" ? prior.suggestion : "",
    };
  }
  const select = document.querySelector("#select"),
    viewer = document.querySelector("#viewer"),
    tools = document.querySelector("#tools"),
    cards = [...document.querySelectorAll(".shot")];
  const current = () => data.items[Number(select.value)];

  // Summary bar: counters, export, print, storage status.
  const summary = document.createElement("div");
  summary.className = "review-summary";
  summary.innerHTML =
    '<p data-summary></p><div class="summary-actions"><button type="button" id="next-pending">Próximo pendente →</button><button type="button" id="export-review" title="Baixa um arquivo com tudo o que você decidiu. É o que você manda de volta pro agente.">Salvar decisões</button><button type="button" id="print-review">Imprimir / PDF</button></div><span data-storage-status role="status"></span><p class="export-done" role="status" aria-live="polite"></p>';
  document.querySelector(".review-toolbar")?.remove();

  // Gallery: status tag per card, pending filter.
  cards.forEach((card) => {
    const tag = document.createElement("span");
    tag.className = "status-tag";
    card.append(tag);
  });
  const pendingOnly = document.querySelector("#pending-only");

  // Storyboard animation modes: static / hover / on.
  const thumbs = [...document.querySelectorAll("[data-animated-thumb]")];
  const visible = new Set();
  let storyboardMode = "static";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function paintThumb(img) {
    const shot = img.closest(".shot");
    const playing =
      !reduced &&
      (storyboardMode === "on"
        ? visible.has(img)
        : storyboardMode === "hover" && (shot.matches(":hover") || shot === document.activeElement));
    const src = playing ? img.dataset.animatedThumb : img.dataset.still;
    if (img.getAttribute("src") !== src) img.setAttribute("src", src);
  }
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(({ target, isIntersecting }) => {
          if (isIntersecting) visible.add(target);
          else visible.delete(target);
          paintThumb(target);
        });
      },
      { threshold: 0.01 },
    );
    thumbs.forEach((img) => observer.observe(img));
  }
  thumbs.forEach((img) => {
    const shot = img.closest(".shot");
    ["pointerenter", "pointerleave", "focus", "blur"].forEach((event) =>
      shot.addEventListener(event, () => paintThumb(img)),
    );
    paintThumb(img);
  });
  document.querySelectorAll("[data-storyboard-mode]").forEach((button) => {
    button.onclick = () => {
      storyboardMode = button.dataset.storyboardMode;
      document
        .querySelectorAll("[data-storyboard-mode]")
        .forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      thumbs.forEach(paintThumb);
    };
  });

  function counts() {
    const all = Object.values(decisions);
    const n = (s) => all.filter((d) => d.state === s).length;
    return { approved: n("approved"), pending: n("pending"), other: all.length - n("approved") - n("pending") };
  }
  function paintGallery() {
    cards.forEach((card, i) => {
      const d = decisions[data.items[i].id];
      const tag = card.querySelector(".status-tag");
      tag.textContent = LABELS[d.state];
      tag.dataset.state = d.state;
      card.hidden = !!pendingOnly?.checked && d.state !== "pending";
    });
    const c = counts();
    const text = `${c.approved} aprovados · ${c.other} com pedidos · ${c.pending} pendentes`;
    document.querySelectorAll("[data-summary]").forEach((el) => (el.textContent = text));
    const next = document.querySelector("#next-pending");
    if (next) next.disabled = c.pending === 0;
  }
  function persist() {
    try {
      localStorage.setItem(key, JSON.stringify(decisions));
    } catch {
      available = false;
    }
    document.querySelectorAll("[data-storage-status]").forEach(
      (el) =>
        (el.textContent = available
          ? "Suas escolhas ficam guardadas nesta aba. Clique em “Salvar decisões” quando terminar."
          : "Esta aba não consegue guardar suas escolhas. Clique em “Salvar decisões” assim que terminar."),
    );
    paintGallery();
    paintPlayer();
  }
  function paintPlayer() {
    const box = viewer.querySelector(".presenter .image-box");
    if (!box) return;
    let badge = box.querySelector(".player-status");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "player-status";
      box.append(badge);
    }
    const d = decisions[current().id];
    badge.textContent = LABELS[d.state];
    badge.dataset.state = d.state;
  }
  function mountPlayer() {
    const presenter = viewer.querySelector(".presenter");
    if (!presenter) return;
    if (tools && !presenter.contains(tools)) presenter.append(tools);
    const gif = presenter.querySelector("[data-gif]");
    if (gif && !presenter.querySelector(".gif-control")) {
      const control = document.createElement("button");
      control.type = "button";
      control.className = "gif-control";
      const sync = () => {
        const playing = gif.getAttribute("aria-pressed") === "true";
        control.innerHTML = playing
          ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3v14H7zm7 0h3v14h-3z"/></svg>'
          : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg>';
        const label = playing ? "Pausar GIF" : "Reproduzir GIF";
        control.title = label;
        control.setAttribute("aria-label", label);
      };
      control.onclick = () => {
        gif.click();
        sync();
      };
      gif.addEventListener("click", sync);
      presenter.querySelector(".image-box").append(control);
      sync();
    }
    paintPlayer();
  }
  function mountPanel() {
    const panel = viewer.querySelector(".review-panel");
    if (!panel || panel.dataset.mounted) return;
    panel.dataset.mounted = "1";
    const material = viewer.querySelector(".material");
    if (material && !material.contains(summary)) material.prepend(summary);
    const item = current(),
      d = decisions[item.id];
    const comment = panel.querySelector("[data-comment]"),
      suggestion = panel.querySelector("[data-suggestion]"),
      status = panel.querySelector("[data-review-status]"),
      fields = panel.querySelector(".review-fields"),
      toggle = panel.querySelector(".comment-toggle"),
      wantOther = panel.querySelector(".want-other"),
      alternative = panel.querySelector("[data-alternative]"),
      other = panel.querySelector(".other-url"),
      confirm = panel.querySelector(".confirm-review");
    let pending = null;
    comment.value = d.comment;
    suggestion.value = d.suggestion;
    alternative.checked = d.state === "alternative";
    // "Outra fonte" virou esta caixinha: mesmo valor exportado, um botão a menos.
    const wanted = () => (alternative.checked ? "alternative" : "changes");
    function reveal(open, asking = false) {
      fields.hidden = !open;
      wantOther.hidden = !asking;
      other.hidden = !(asking && alternative.checked);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Fechar comentário" : comment.value ? "Ver comentário" : "Comentar";
    }
    function checkComment() {
      // Validação na hora: o bloqueio aparece onde a pessoa escreve, não no fim —
      // inclusive quando ela apaga o comentário de um pedido de ajuste já confirmado.
      if (!pending && !["changes", "alternative"].includes(d.state)) return;
      const empty = !comment.value.trim();
      confirm.disabled = empty;
      confirm.textContent =
        wanted() === "changes" ? "Confirmar pedido de ajuste" : "Confirmar: procure outro vídeo";
      if (empty) status.textContent = "Me conta em uma linha o que você queria.";
      else if (pending) status.textContent = "";
      else paint();
    }
    function paint() {
      panel.querySelectorAll("[data-decision]").forEach((b) =>
        b.setAttribute(
          "aria-pressed",
          String(
            b.dataset.decision === d.state ||
              (b.dataset.decision === "changes" && d.state === "alternative"),
          ),
        ),
      );
      status.textContent = d.state === "pending" ? "" : LABELS[d.state] + (d.updatedAt ? " · guardado nesta aba" : "");
    }
    toggle.onclick = () => {
      pending = null;
      confirm.hidden = true;
      reveal(fields.hidden, ["changes", "alternative"].includes(d.state));
      if (!fields.hidden) comment.focus();
    };
    alternative.onchange = () => {
      other.hidden = !alternative.checked;
      checkComment();
    };
    panel.querySelectorAll("[data-decision]").forEach((b) => {
      b.onclick = () => {
        const state = b.dataset.decision;
        if (state === d.state || (state === "changes" && d.state === "alternative")) {
          // Clicking the active decision undoes it.
          d.state = "pending";
          d.updatedAt = new Date().toISOString();
          pending = null;
          confirm.hidden = true;
          reveal(false);
          paint();
          persist();
          return;
        }
        if (state === "changes") {
          pending = state;
          reveal(true, true);
          confirm.hidden = false;
          checkComment();
          comment.focus();
          return;
        }
        pending = null;
        confirm.hidden = true;
        reveal(false);
        d.state = state;
        d.updatedAt = new Date().toISOString();
        paint();
        persist();
      };
    });
    confirm.onclick = () => {
      if (!comment.value.trim()) {
        status.textContent = "Me conta em uma linha o que você queria.";
        comment.focus();
        return;
      }
      d.state = wanted();
      d.updatedAt = new Date().toISOString();
      pending = null;
      confirm.hidden = true;
      reveal(false);
      paint();
      persist();
    };
    comment.oninput = () => {
      d.comment = comment.value;
      checkComment();
      persist();
    };
    suggestion.oninput = () => {
      d.suggestion = suggestion.value;
      persist();
    };
    reveal(false, ["changes", "alternative"].includes(d.state));
    paint();
    wireSummary();
  }
  function wireSummary() {
    const next = document.querySelector("#next-pending");
    if (next && !next.dataset.wired) {
      next.dataset.wired = "1";
      next.onclick = () => {
        const start = Number(select.value);
        const n = data.items.length;
        for (let step = 1; step <= n; step++) {
          const i = (start + step) % n;
          if (decisions[data.items[i].id].state === "pending") {
            window.getbrollsGo?.(i);
            return;
          }
        }
      };
    }
    const exportButton = document.querySelector("#export-review");
    if (exportButton && !exportButton.dataset.wired) {
      exportButton.dataset.wired = "1";
      exportButton.onclick = exportReview;
    }
    const printButton = document.querySelector("#print-review");
    if (printButton && !printButton.dataset.wired) {
      printButton.dataset.wired = "1";
      printButton.onclick = () => {
        buildPrintNotes();
        window.print();
      };
    }
  }
  function mount() {
    mountPlayer();
    mountPanel();
  }
  new MutationObserver(mount).observe(viewer, { childList: true });
  if (pendingOnly) pendingOnly.onchange = paintGallery;
  mount();
  persist();

  function exportReview() {
    const note = (text) =>
      document.querySelectorAll("[data-storage-status]").forEach((el) => (el.textContent = text));
    for (const d of Object.values(decisions)) {
      if (["changes", "alternative"].includes(d.state) && !d.comment.trim()) {
        note("Falta dizer o que mudar em um dos trechos. Abra o trecho e escreva uma linha.");
        return;
      }
      if (d.suggestion) {
        let valid = false;
        try {
          const u = new URL(d.suggestion);
          valid =
            u.protocol === "https:" &&
            !u.username &&
            !u.password &&
            ![...u.searchParams.keys()].some((k) =>
              /^(key|api_key|apikey|token|access_token|authorization|signature|sig)$|^x-amz-|^x-goog-/i.test(k),
            ) &&
            u.hostname.includes(".") &&
            u.hostname !== "localhost" &&
            !u.hostname.endsWith(".local") &&
            !/^\d+\.\d+\.\d+\.\d+$/.test(u.hostname);
        } catch {}
        if (!valid) {
          note("O link de outro vídeo precisa ser um endereço https público, sem senha nem código de acesso. Corrija ou apague o link.");
          return;
        }
      }
    }
    const result = {
      ...data,
      exportedAt: new Date().toISOString(),
      items: data.items.map((i) => ({ ...i, ...decisions[i.id] })),
    };
    const save = window.GETBROLLS_SAVE;
    if (save && save.url && save.token) {
      // Servido por `gb.py serve`: as decisões vão direto para dentro do projeto,
      // sem passar pela pasta de Downloads nem depender de a pessoa achar o arquivo.
      note("Salvando no projeto…");
      fetch(save.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", [save.header]: save.token },
        body: JSON.stringify(result),
      })
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        })
        .then((answer) => {
          note("");
          announce(
            "Decisões salvas no projeto (" +
              answer.name +
              "). É só voltar à conversa e dizer “salvei”.",
            answer.path,
          );
        })
        .catch(() => {
          // Servidor fora do ar ou recusa: cai no download, que nunca depende dele.
          note("Não consegui salvar no projeto; baixei o arquivo em vez disso.");
          download(result);
        });
      return;
    }
    download(result);
  }
  function download(result) {
    const note = (text) =>
      document.querySelectorAll("[data-storage-status]").forEach((el) => (el.textContent = text));
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "getbrolls-review.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    // O maior buraco da jornada era aqui: a página acabava e ninguém dizia pra voltar.
    announce(
      "Decisões salvas em getbrolls-review.json (na sua pasta de Downloads). " +
        "Agora volte à conversa e diga onde salvou.",
      "getbrolls-review.json",
    );
    note("");
  }
  function announce(text, path) {
    // A região viva já nasce montada e vazia junto da barra de resumo, no load: um
    // `role="status"` inserido no DOM já preenchido costuma não ser anunciado pelo
    // leitor de tela. Aqui só trocamos o texto, sem `setTimeout` — o anúncio vira
    // parte do mesmo passo do export, e o teste consegue observar o resultado.
    const done = document.querySelector(".export-done");
    if (!done) return;
    done.textContent = text;
    if (!path || !navigator.clipboard) return;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-path";
    copy.textContent = "Copiar caminho";
    copy.title = "Copia o caminho do arquivo de decisões para você colar na conversa.";
    copy.onclick = () =>
      navigator.clipboard.writeText(path).then(
        () => (copy.textContent = "Caminho copiado"),
        () => (copy.textContent = path),
      );
    done.append(" ", copy);
  }
  function buildPrintNotes() {
    document.querySelector(".print-notes")?.remove();
    const section = document.createElement("section");
    section.className = "print-notes";
    data.items.forEach((item) => {
      const article = document.createElement("article"),
        h = document.createElement("h2");
      h.textContent = item.title;
      article.append(h);
      for (const source of [item.poster, item.context_poster].filter(Boolean)) {
        const poster = document.createElement("img");
        poster.src = source;
        poster.alt = item.title;
        poster.loading = "eager";
        article.append(poster);
      }
      const d = decisions[item.id];
      for (const text of [
        item.asset_type && item.asset_type !== "video"
          ? `Imagem estática${item.captured_at ? " · Capturada em " + item.captured_at : ""}`
          : item.segment.start_s === null
            ? "Intervalo a definir"
            : `Trecho: ${item.segment.start_s}–${item.segment.end_s} s`,
        item.source ? "Fonte: " + item.source : "Arquivo local",
        item.creator ? "Autor: " + item.creator : "",
        item.narration ? "Fala: “" + item.narration + "”" : "",
        item.collection_reason ? "Coleta: " + item.collection_reason : "",
        "Revisão: " + LABELS[d.state],
        d.comment ? "Comentário: " + d.comment : "",
        d.suggestion ? "Sugestão: " + d.suggestion : "",
      ].filter(Boolean)) {
        const p = document.createElement("p");
        p.textContent = text;
        article.append(p);
      }
      section.append(article);
    });
    document.querySelector("main").append(section);
  }
  window.addEventListener("beforeprint", buildPrintNotes);
})();
