(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    input: $("input"),
    mode: $("mode"),
    modeValue: $("mode_value"),
    minScore: $("min_score"),
    ruPostpass: $("ru_postpass"),
    run: $("run"),
    detect: $("detect_only"),
    clear: $("clear"),
    status: $("status"),
    highlight: $("highlight"),
    redacted: $("redacted"),
    sample: $("sample"),
    spansBody: document.querySelector("#spans tbody"),
    meta: $("meta"),
    upload: $("upload"),
    uploadBtn: $("upload_btn"),
    filename: $("filename"),
    dropzone: $("dropzone"),
    copyRedacted: $("copy_redacted"),
    downloadTxt: $("download_txt"),
    downloadJson: $("download_json"),
    themeToggle: $("theme_toggle"),
    themeIcon: $("theme_icon"),
  };

  let lastResult = { spans: [], redacted: "", input: "" };

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
    const ms = parseFloat(els.minScore.value);
    if (!Number.isNaN(ms)) body.min_score = ms;
    body.ru_postpass = els.ruPostpass.checked;
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
      lastResult = { spans, redacted: data.redacted ?? "", input: text };
      const cached = data.cached ? " · cached" : "";
      setStatus(`OK — ${spans.length} span(s) in ${dt} ms · model: ${data.model}${cached}`);
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
        if (name && data[name]) {
          els.input.value = data[name];
          els.filename.textContent = `sample: ${name}`;
        }
      });
    } catch {
      /* ignore */
    }
  };

  const loadHealth = async () => {
    try {
      const resp = await fetch("/health");
      const data = await resp.json();
      const dom = data.domain || location.host;
      els.meta.textContent = `Model: ${data.model} · ${dom}`;
    } catch {
      els.meta.textContent = "Service unreachable.";
    }
  };

  const readFile = (file) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setStatus(`Файл слишком большой (${(file.size / 1024 / 1024).toFixed(1)} MB). Лимит 5 MB.`, true);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      els.input.value = String(reader.result || "");
      els.filename.textContent = `${file.name} · ${file.size} B`;
      els.sample.value = "";
      setStatus(`Loaded ${file.name}.`);
    };
    reader.onerror = () => setStatus(`Не удалось прочитать файл: ${reader.error}`, true);
    reader.readAsText(file, "utf-8");
  };

  const downloadBlob = (filename, mime, content) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const setupTheme = () => {
    const stored = localStorage.getItem("pf-theme");
    const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
    const initial = stored || (prefersLight ? "light" : "dark");
    applyTheme(initial);
    els.themeToggle.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
      localStorage.setItem("pf-theme", next);
    });
  };

  const applyTheme = (name) => {
    document.documentElement.dataset.theme = name;
    els.themeIcon.textContent = name === "light" ? "☀" : "☾";
  };

  els.run.addEventListener("click", () => run("redact"));
  els.detect.addEventListener("click", () => run("detect"));
  els.clear.addEventListener("click", () => {
    els.input.value = "";
    els.highlight.textContent = "";
    els.redacted.textContent = "";
    els.spansBody.innerHTML = "";
    els.filename.textContent = "";
    els.sample.value = "";
    lastResult = { spans: [], redacted: "", input: "" };
    setStatus("");
  });
  els.mode.addEventListener("change", onModeChange);

  els.uploadBtn.addEventListener("click", () => els.upload.click());
  els.upload.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    readFile(file);
    els.upload.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
    })
  );
  els.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    readFile(file);
  });

  els.copyRedacted.addEventListener("click", async () => {
    if (!lastResult.redacted) return setStatus("Сначала запустите Redact.", true);
    try {
      await navigator.clipboard.writeText(lastResult.redacted);
      setStatus("Redacted text copied to clipboard.");
    } catch (e) {
      setStatus(`Clipboard error: ${e.message}`, true);
    }
  });
  els.downloadTxt.addEventListener("click", () => {
    if (!lastResult.redacted) return setStatus("Сначала запустите Redact.", true);
    downloadBlob("redacted.txt", "text/plain;charset=utf-8", lastResult.redacted);
  });
  els.downloadJson.addEventListener("click", () => {
    if (!lastResult.spans.length && !lastResult.redacted) {
      return setStatus("Сначала запустите Redact или Detect.", true);
    }
    const payload = JSON.stringify(lastResult, null, 2);
    downloadBlob("result.json", "application/json", payload);
  });

  setupTheme();
  onModeChange();
  loadSamples();
  loadHealth();
})();
