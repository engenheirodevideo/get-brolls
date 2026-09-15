(() => {
  "use strict";
  const data = window.GETBROLLS_REVIEW;
  if (!data.items.length) return;
  const key = "getbrolls-v2:" + data.project;
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
      old &&
      old.signature === item.signature &&
      old.reviewEpoch === item.reviewEpoch
        ? old
        : item.review;
    decisions[item.id] = {
      signature: item.signature,
      reviewEpoch: item.reviewEpoch,
      state: ["pending", "approved", "changes", "alternative"].includes(
        prior?.state,
      )
        ? prior.state
        : "pending",
      comment: typeof prior?.comment === "string" ? prior.comment : "",
      suggestion: typeof prior?.suggestion === "string" ? prior.suggestion : "",
    };
  }
  const select = document.querySelector("#select");
  const current = () => data.items[Number(select.value)];
  function persist() {
    try {
      localStorage.setItem(key, JSON.stringify(decisions));
    } catch {
      available = false;
    }
    document.querySelector("[data-storage-status]").textContent = available
      ? "Salvo neste navegador. Exporte para enviar."
      : "Salvamento local indisponível. Exporte antes de fechar.";
    summary();
  }
  function summary() {
    const n = Object.values(decisions).filter(
      (x) => x.state === "approved",
    ).length;
    document.querySelector("[data-summary]").textContent =
      `${n} de ${data.items.length} aprovados`;
  }
  function mount() {
    const panel = document.querySelector(".review-panel");
    if (!panel) return;
    const item = current(),
      d = decisions[item.id];
    const comment = panel.querySelector("[data-comment]"),
      suggestion = panel.querySelector("[data-suggestion]"),
      status = panel.querySelector("[data-review-status]");
    comment.value = d.comment;
    suggestion.value = d.suggestion;
    function paint() {
      panel
        .querySelectorAll("[data-decision]")
        .forEach((b) =>
          b.setAttribute(
            "aria-pressed",
            String(b.dataset.decision === d.state),
          ),
        );
      status.textContent = {
        pending: "Pendente",
        approved: "Aprovado",
        changes: "Ajuste solicitado",
        alternative: "Outra fonte solicitada",
      }[d.state];
    }
    comment.oninput = () => {
      d.comment = comment.value;
      persist();
    };
    suggestion.oninput = () => {
      d.suggestion = suggestion.value;
      persist();
    };
    panel.querySelectorAll("[data-decision]").forEach(
      (b) =>
        (b.onclick = () => {
          if (
            ["changes", "alternative"].includes(b.dataset.decision) &&
            !d.comment.trim()
          ) {
            status.textContent = "Descreva a alteração ou a fonte desejada.";
            comment.focus();
            return;
          }
          d.state = b.dataset.decision;
          d.updatedAt = new Date().toISOString();
          paint();
          persist();
        }),
    );
    paint();
  }
  new MutationObserver(mount).observe(document.querySelector("#viewer"), {
    childList: true,
  });
  mount();
  persist();
  document.querySelector("#export-review").onclick = () => {
    for (const d of Object.values(decisions)) {
      if (["changes", "alternative"].includes(d.state) && !d.comment.trim()) {
        document.querySelector("[data-storage-status]").textContent =
          "Preencha o comentário de cada ajuste ou sugestão antes de exportar.";
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
              /^(key|api_key|apikey|token|access_token|authorization|signature|sig)$|^x-amz-|^x-goog-/i.test(
                k,
              ),
            ) &&
            u.hostname.includes(".") &&
            u.hostname !== "localhost" &&
            !u.hostname.endsWith(".local") &&
            !/^\d+\.\d+\.\d+\.\d+$/.test(u.hostname);
        } catch {}
        if (!valid) {
          document.querySelector("[data-storage-status]").textContent =
            "Corrija a sugestão: use URL HTTPS pública, sem credenciais ou parâmetros secretos.";
          return;
        }
      }
    }
    const result = {
      ...data,
      exportedAt: new Date().toISOString(),
      items: data.items.map((i) => ({ ...i, ...decisions[i.id] })),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "getbrolls-review.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  function buildPrintNotes() {
    document.querySelector(".print-notes")?.remove();
    const section = document.createElement("section");
    section.className = "print-notes";
    data.items.forEach((item, i) => {
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
        "Revisão: " +
          {
            pending: "Pendente",
            approved: "Aprovado",
            changes: "Ajuste solicitado",
            alternative: "Outra fonte solicitada",
          }[decisions[item.id].state],
        decisions[item.id].comment
          ? "Comentário: " + decisions[item.id].comment
          : "",
        decisions[item.id].suggestion
          ? "Sugestão: " + decisions[item.id].suggestion
          : "",
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
  document.querySelector("#print-review").onclick = () => {
    buildPrintNotes();
    window.print();
  };
})();

