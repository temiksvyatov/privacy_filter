(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    input: $("input"),
    mode: $("mode"),
    modeValue: $("mode_value"),
    run: $("run"),
    detect: $("detect_only"),
    clear: $("clear"),
    status: $("status"),
    highlight: $("highlight"),
    redacted: $("redacted"),
    sample: $("sample"),
    spansBody: document.querySelector("#spans tbody"),
    meta: $("meta"),
  };

  const escapeHtml = (s) =>
    s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const setStatus = (msg, isError = false) => {
    els.status.textContent = msg || "";
    els.status.classList.toggle("error", !!isError);
  };

  const buildBody = () => {
    const text = els.input.value;
    const body = { text };
    const mode = els.mode.value;
    const v = els.modeValue.value;
    if (mode === "placeholder" && v) body.placeholder = v;
    if (mode === "mask_char" && v) body.mask_char = v.charAt(0);
    return body;
  };

  const onModeChange = () => {
    const mode = els.mode.value;
    if (mode === "tag") {
      els.modeValue.value = "";
      els.modeValue.disabled = true;
      els.modeValue.placeholder = "(no value)";
    } else if (mode === "placeholder") {
      els.modeValue.disabled = false;
      els.modeValue.placeholder = "[REDACTED]";
      if (!els.modeValue.value) els.modeValue.value = "[REDACTED]";
    } else {
      els.modeValue.disabled = false;
      els.modeValue.placeholder = "*";
      if (!els.modeValue.value) els.modeValue.value = "*";
      if (els.modeValue.value.length > 1) els.modeValue.value = els.modeValue.value.charAt(0);
    }
  };

  const renderHighlight = (text, spans) => {
    if (!spans.length) {
      els.highlight.textContent = text || "(empty)";
      return;
    }
    const ordered = [...spans].sort((a, b) => a.start - b.start);
    let html = "";
    let cursor = 0;
    for (const s of ordered) {
      if (s.start < cursor) continue;
      html += escapeHtml(text.slice(cursor, s.start));
      html += `<span class="pii" data-entity="${escapeHtml(s.entity)}" title="${escapeHtml(s.entity)} (${s.score.toFixed(3)})">${escapeHtml(text.slice(s.start, s.end))}</span>`;
      cursor = s.end;
    }
    html += escapeHtml(text.slice(cursor));
    els.highlight.innerHTML = html;
  };

  const renderSpans = (spans) => {
    if (!spans.length) {
      els.spansBody.innerHTML = `<tr><td colspan="4" class="muted">No PII detected.</td></tr>`;
      return;
    }
    els.spansBody.innerHTML = spans.map((s) => `
      <tr>
        <td>${escapeHtml(s.entity)}</td>
        <td class="text">${escapeHtml(s.text)}</td>
        <td>${s.start}–${s.end}</td>
        <td>${s.score.toFixed(4)}</td>
      </tr>
    `).join("");
  };

  const callApi = async (path, payload) => {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${detail}`);
    }
    return resp.json();
  };

  const run = async (mode) => {
    const text = els.input.value;
    if (!text.trim()) {
      setStatus("Введите или выберите текст.", true);
      return;
    }
    els.run.disabled = els.detect.disabled = true;
    setStatus("Working…");
    try {
      const t0 = performance.now();
      const path = mode === "redact" ? "/redact" : "/detect";
      const data = await callApi(path, buildBody());
      const dt = (performance.now() - t0).toFixed(0);
      const spans = data.spans || [];
      renderHighlight(text, spans);
      renderSpans(spans);
      els.redacted.textContent = data.redacted ?? "(detect-only mode)";
      setStatus(`OK — ${spans.length} span(s) in ${dt} ms · model: ${data.model}`);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      els.run.disabled = els.detect.disabled = false;
    }
  };

  const loadSamples = async () => {
    try {
      const resp = await fetch("/samples");
      const data = await resp.json();
      for (const name of Object.keys(data)) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        els.sample.appendChild(opt);
      }
      els.sample.addEventListener("change", () => {
        const name = els.sample.value;
        if (name && data[name]) els.input.value = data[name];
      });
    } catch {
      /* ignore */
    }
  };

  const loadHealth = async () => {
    try {
      const resp = await fetch("/health");
      const data = await resp.json();
      els.meta.textContent = `Model: ${data.model} · Domain: ${location.host}`;
    } catch {
      els.meta.textContent = "Service unreachable.";
    }
  };

  els.run.addEventListener("click", () => run("redact"));
  els.detect.addEventListener("click", () => run("detect"));
  els.clear.addEventListener("click", () => {
    els.input.value = "";
    els.highlight.textContent = "";
    els.redacted.textContent = "";
    els.spansBody.innerHTML = "";
    setStatus("");
  });
  els.mode.addEventListener("change", onModeChange);

  onModeChange();
  loadSamples();
  loadHealth();
})();
