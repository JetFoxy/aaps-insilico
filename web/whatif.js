(() => {
  "use strict";

  const el = (id) => document.getElementById(id);
  const t = (key, vars) => window.t(key, vars);
  const REAL_COLOR = "#4fc3f7";
  const REAL_BOLUS_COLOR = "#90a4ae";
  const VARIANT_COLORS = ["#ffb74d", "#ce93d8", "#81c784", "#f06292"];
  const PREF_KEYS = [
    "bgAccel_ISF_weight", "bgBrake_ISF_weight", "lower_ISFrange_weight",
    "higher_ISFrange_weight", "pp_ISF_weight", "dura_ISF_weight",
    "autoISF_min", "autoISF_max",
  ];
  const PREF_LABELS = {
    bgAccel_ISF_weight: "params.weight.accel",
    bgBrake_ISF_weight: "params.weight.brake",
    lower_ISFrange_weight: "params.weight.lower",
    higher_ISFrange_weight: "params.weight.higher",
    pp_ISF_weight: "params.weight.pp",
    dura_ISF_weight: "params.weight.dura",
    autoISF_min: "params.limit.min",
    autoISF_max: "params.limit.max",
  };
  const OVERRIDES = [
    ["isf", "whatif.override.isf"],
    ["carb_ratio", "whatif.override.carbRatio"],
    ["basal", "whatif.override.basal"],
    ["target", "whatif.override.target"],
  ];
  const FALLBACK_OPTIONS = {
    modes: ["replay", "model"],
    algorithms: ["off", "autoisf", "dynisf"],
    prefs: {
      bgAccel_ISF_weight: 0.36, bgBrake_ISF_weight: 0.37,
      lower_ISFrange_weight: 0.27, higher_ISFrange_weight: 0.76,
      pp_ISF_weight: 0, dura_ISF_weight: 0.15,
      autoISF_min: 0.7, autoISF_max: 1.3,
    },
    defaults: { days: 3, max_days: 14, max_variants: 4, cadence_min: 5 },
  };

  const state = {
    options: null,
    traceId: null,
    summary: null,
    mode: "replay",
    variants: [],
    results: null,
    run: null,
    series: [],
    source: null,
    busy: false,
    drawQueued: false,
    realSeries: null,
    realTrace: null,
    realInsulin: [],
    real: null,
    statuses: {},
  };

  const locale = () => (window.SIM_LANG === "ru" ? "ru-RU" : "en-GB");
  const maxVariants = () => state.options?.defaults?.max_variants || 4;
  const color = (index) => VARIANT_COLORS[index % VARIANT_COLORS.length];

  function fmt(value, digits = 1) {
    const number = Number(value);
    if (value === null || value === undefined || !Number.isFinite(number)) {
      return t("whatif.metrics.na");
    }
    return number.toLocaleString(locale(), {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    });
  }

  function pct(value) {
    const number = Number(value);
    if (value === null || value === undefined || !Number.isFinite(number)) {
      return t("whatif.metrics.na");
    }
    return `${(number * 100).toFixed(1)} %`;
  }

  function fmtDateTime(ts) {
    const date = new Date(Number(ts));
    if (!Number.isFinite(date.getTime())) return t("whatif.metrics.na");
    return date.toLocaleString(locale(), {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function confidenceLabel(value) {
    if (value === "good" || value === "low") return t(`whatif.confidence.${value}`);
    return value || t("whatif.metrics.na");
  }

  function escapeXml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function newVariant(index) {
    return {
      label: `V${index + 1}`,
      algorithm: "autoisf",
      smb: true,
      prefs: {},
      profile_overrides: {},
    };
  }

  function setStatus(name, spec) {
    state.statuses[name] = spec;
    paintStatus(name);
  }

  function paintStatus(name) {
    const node = el(name === "connect" ? "connect-status" : name === "import" ? "import-status" : "run-status");
    if (!node) return;
    const spec = state.statuses[name];
    node.textContent = spec ? (spec.raw ?? t(spec.key, spec.vars)) : "";
    node.classList.toggle("error", Boolean(spec && spec.error));
    node.classList.toggle("ok", Boolean(spec && spec.ok));
  }

  function repaintStatuses() {
    Object.keys(state.statuses).forEach(paintStatus);
  }

  async function loadOptions() {
    try {
      const response = await fetch("/api/whatif/options");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.options = await response.json();
    } catch {
      state.options = JSON.parse(JSON.stringify(FALLBACK_OPTIONS));
    }
    const days = el("ns-days");
    days.max = String(state.options.defaults.max_days || 14);
    days.value = String(state.options.defaults.days || 3);
    renderVariants();
  }

  function variantCard(variant, index) {
    const card = document.createElement("div");
    card.className = "variant";
    card.dataset.index = String(index);

    const prefs = PREF_KEYS.map((key) => `
      <label><span>${escapeXml(t(PREF_LABELS[key]))}</span>
        <input type="number" step="0.01" data-pref="${key}"></label>`).join("");
    const overrides = OVERRIDES.map(([key, label]) => `
      <label><span>${escapeXml(t(label))}</span>
        <input type="number" step="0.1" data-override="${key}"></label>`).join("");

    card.innerHTML = `
      <div class="variant-head">
        <input class="variant-label" type="text" maxlength="48" aria-label="${escapeXml(t("whatif.variant.label"))}">
        <button type="button" class="ghost copy" data-i18n="whatif.variant.copy"></button>
        <button type="button" class="ghost reset" data-i18n="whatif.variant.reset"></button>
        <button type="button" class="ghost remove" data-i18n="whatif.variant.remove"></button>
      </div>
      <div class="variant-grid">
        <label><span data-i18n="whatif.variant.algorithm"></span>
          <select class="variant-algorithm">
            <option value="off" data-i18n="whatif.algorithm.off"></option>
            <option value="autoisf" data-i18n="whatif.algorithm.autoisf"></option>
            <option value="dynisf" data-i18n="whatif.algorithm.dynisf"></option>
          </select></label>
        <label class="chk"><input type="checkbox" class="variant-smb">
          <span data-i18n="whatif.variant.smb"></span></label>
      </div>
      <details class="variant-details">
        <summary data-i18n="whatif.variant.weights"></summary>
        <div class="variant-grid weights">${prefs}</div>
      </details>
      <details class="variant-details">
        <summary data-i18n="whatif.variant.overrides"></summary>
        <div class="variant-grid">${overrides}</div>
      </details>`;

    card.querySelector(".variant-label").value = variant.label;
    card.querySelector(".variant-algorithm").value = variant.algorithm;
    card.querySelector(".variant-smb").checked = Boolean(variant.smb);

    PREF_KEYS.forEach((key) => {
      const input = card.querySelector(`input[data-pref="${key}"]`);
      const fallback = state.options?.prefs?.[key];
      input.placeholder = fallback === null || fallback === undefined ? "" : String(fallback);
      if (variant.prefs[key] !== undefined) input.value = String(variant.prefs[key]);
    });
    OVERRIDES.forEach(([key]) => {
      const input = card.querySelector(`input[data-override="${key}"]`);
      if (variant.profile_overrides[key] !== undefined) {
        input.value = String(variant.profile_overrides[key]);
      }
    });

    card.querySelector(".remove").disabled = state.variants.length <= 1;
    return card;
  }

  function renderVariants() {
    const list = el("variant-list");
    list.innerHTML = "";
    state.variants.forEach((variant, index) => {
      list.appendChild(variantCard(variant, index));
    });
    el("add-variant").disabled = state.variants.length >= maxVariants();
    window.applyI18n?.();
  }

  function numberOrNull(value) {
    if (value === null || value === undefined || String(value).trim() === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function onVariantInput(event) {
    const target = event.target;
    const card = target.closest(".variant");
    if (!card) return;
    const index = Number(card.dataset.index);
    const variant = state.variants[index];
    if (!variant) return;
    if (target.classList.contains("variant-label")) {
      variant.label = target.value;
    } else if (target.classList.contains("variant-algorithm")) {
      variant.algorithm = target.value;
    } else if (target.classList.contains("variant-smb")) {
      variant.smb = target.checked;
    } else if (target.dataset.pref) {
      const value = numberOrNull(target.value);
      if (value === null) delete variant.prefs[target.dataset.pref];
      else variant.prefs[target.dataset.pref] = value;
    } else if (target.dataset.override) {
      const value = numberOrNull(target.value);
      if (value === null) delete variant.profile_overrides[target.dataset.override];
      else variant.profile_overrides[target.dataset.override] = value;
    }
  }

  function onVariantClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    const card = button.closest(".variant");
    if (!card) return;
    const index = Number(card.dataset.index);
    const variant = state.variants[index];
    if (!variant) return;
    if (button.classList.contains("copy")) {
      if (state.variants.length >= maxVariants()) return;
      const copy = JSON.parse(JSON.stringify(variant));
      copy.label = `${variant.label} ${state.variants.length + 1}`;
      state.variants.splice(index + 1, 0, copy);
      renderVariants();
    } else if (button.classList.contains("reset")) {
      const reset = newVariant(index);
      reset.label = variant.label;
      state.variants[index] = reset;
      renderVariants();
    } else if (button.classList.contains("remove")) {
      if (state.variants.length <= 1) return;
      state.variants.splice(index, 1);
      renderVariants();
    }
  }

  function collectVariants() {
    return state.variants.map((variant, index) => {
      const prefs = {};
      const overrides = {};
      Object.entries(variant.prefs || {}).forEach(([key, value]) => {
        if (Number.isFinite(Number(value))) prefs[key] = Number(value);
      });
      Object.entries(variant.profile_overrides || {}).forEach(([key, value]) => {
        if (Number.isFinite(Number(value))) overrides[key] = Number(value);
      });
      const algorithm = ["off", "autoisf", "dynisf"].includes(variant.algorithm)
        ? variant.algorithm : "autoisf";
      return {
        label: String(variant.label || `V${index + 1}`).slice(0, 48),
        algorithm,
        smb: Boolean(variant.smb),
        prefs,
        profile_overrides: overrides,
      };
    });
  }

  function renderSummary() {
    const box = el("import-summary");
    box.innerHTML = "";
    if (!state.summary) return;
    const summary = state.summary;
    const row = (text, cls) => {
      const node = document.createElement("div");
      node.className = `sum-row${cls ? ` ${cls}` : ""}`;
      node.textContent = text;
      box.appendChild(node);
    };
    row(t("whatif.summary.entries", { n: summary.entries }));
    row(t("whatif.summary.treatments", { n: summary.treatments }));
    row(t("whatif.summary.decisions", { n: summary.decisions }));
    row(t("whatif.summary.profiles", { n: summary.profiles }));
    row(t("whatif.summary.window", {
      start: fmtDateTime(summary.window_start),
      end: fmtDateTime(summary.window_end),
    }));
    if (summary.sensor) row(t("whatif.summary.sensor", { name: summary.sensor }));
    row(
      t(summary.prefs_loaded ? "whatif.summary.prefs.yes" : "whatif.summary.prefs.no"),
      summary.prefs_loaded ? "ok" : "warn",
    );
    row(t("whatif.summary.warnings"), "muted");
    const warnings = summary.warnings || [];
    if (!warnings.length) row(t("whatif.summary.noWarnings"), "muted");
    warnings.forEach((warning) => row(String(warning), "warn"));
    if (!summary.prefs_loaded) row(t("whatif.summary.exportMissing"), "warn");
  }

  function syncModeAvailability() {
    const blocked = Boolean(state.summary && state.summary.prefs_loaded === false);
    const replay = document.querySelector('input[name="mode"][value="replay"]');
    replay.disabled = blocked;
    el("mode-replay-card").classList.toggle("blocked", blocked);
    if (blocked && state.mode === "replay") {
      state.mode = "model";
      document.querySelector('input[name="mode"][value="model"]').checked = true;
    }
  }

  function renderModeNote() {
    const node = el("mode-note");
    if (!state.summary) {
      node.textContent = "";
      return;
    }
    if (state.mode === "replay" && !state.summary.prefs_loaded) {
      node.textContent = t("whatif.mode.replay.blocked");
    } else if (state.mode === "model" && !state.summary.prefs_loaded) {
      node.textContent = t("whatif.mode.model.prefs");
    } else {
      node.textContent = t("whatif.mode.ready");
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    el("connect").disabled = busy;
    el("run").disabled = busy;
    el("delete-import").disabled = busy;
    el("add-variant").disabled = busy || state.variants.length >= maxVariants();
    document.querySelectorAll('input[name="mode"]').forEach((node) => {
      node.disabled = busy;
    });
    el("variant-list").querySelectorAll("input, select, button").forEach((node) => {
      node.disabled = busy;
    });
    if (!busy) {
      syncModeAvailability();
      renderVariants();
    }
  }

  function showResults() {
    el("results-panel").classList.remove("hidden");
  }

  function clearResults() {
    state.results = null;
    state.run = null;
    state.series = [];
    el("results-badge").textContent = "";
    el("chart").innerHTML = "";
    el("chart-note").textContent = "";
    el("variant-legend").innerHTML = "";
    el("metrics-table").querySelector("thead").innerHTML = "";
    el("metrics-table").querySelector("tbody").innerHTML = "";
    el("metrics-note").textContent = "";
    el("not-reproduced-list").innerHTML = "";
    el("results-panel").classList.add("hidden");
  }

  function closeStream() {
    if (state.source) {
      state.source.close();
      state.source = null;
    }
  }

  async function connect(event) {
    event.preventDefault();
    if (state.busy) {
      setStatus("connect", { key: "whatif.error.busy", error: true });
      return;
    }
    const url = el("ns-url").value.trim();
    const token = el("ns-token").value;
    const apiSecret = el("ns-api-secret").value;
    const days = Number(el("ns-days").value || 3);
    const file = el("prefs-file").files[0];
    const password = el("prefs-password").value;
    const payload = { url, days };
    if (token) payload.token = token;
    if (apiSecret) payload.api_secret = apiSecret;
    if (password) payload.password = password;
    if (file) {
      try {
        payload.prefs_text = await file.text();
      } catch {
        setStatus("connect", { key: "whatif.error.file", error: true });
        return;
      }
    }
    setBusy(true);
    setStatus("connect", { key: "whatif.status.importing" });
    try {
      const response = await fetch("/api/whatif/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.traceId = data.trace_id;
      state.summary = data.summary;
      state.realSeries = null;
      state.realTrace = null;
      clearResults();
      el("ns-token").value = "";
      el("ns-api-secret").value = "";
      el("prefs-password").value = "";
      el("prefs-file").value = "";
      el("connect-panel").classList.add("hidden");
      el("import-panel").classList.remove("hidden");
      renderSummary();
      syncModeAvailability();
      renderModeNote();
      setStatus("connect", { key: "whatif.status.imported", ok: true });
      setStatus("import", { key: "whatif.status.idle" });
    } catch (error) {
      setStatus("connect", {
        raw: error instanceof TypeError ? t("whatif.error.server") : error.message,
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }

  async function deleteImport() {
    if (!state.traceId || state.busy) return;
    if (!window.confirm(t("whatif.delete.confirm"))) return;
    setBusy(true);
    setStatus("import", { key: "whatif.delete.busy" });
    try {
      const response = await fetch(`/api/whatif/trace/${encodeURIComponent(state.traceId)}`, {
        method: "DELETE",
      });
      if (!response.ok && response.status !== 404) {
        throw new Error(`HTTP ${response.status}`);
      }
      closeStream();
      state.traceId = null;
      state.summary = null;
      state.realSeries = null;
      state.realTrace = null;
      clearResults();
      el("import-summary").innerHTML = "";
      el("import-panel").classList.add("hidden");
      el("connect-panel").classList.remove("hidden");
      el("ns-token").value = "";
      el("ns-api-secret").value = "";
      el("prefs-password").value = "";
      el("prefs-file").value = "";
      state.mode = "replay";
      document.querySelector('input[name="mode"][value="replay"]').checked = true;
      syncModeAvailability();
      renderModeNote();
      setStatus("import", null);
      setStatus("connect", { key: "whatif.delete.done", ok: true });
    } catch (error) {
      setStatus("import", {
        raw: error instanceof TypeError ? t("whatif.error.server") : error.message,
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }

  async function startRun() {
    if (state.busy) return;
    if (!state.traceId) {
      setStatus("run", { key: "whatif.error.noTrace", error: true });
      return;
    }
    if (state.mode === "replay" && state.summary && !state.summary.prefs_loaded) {
      setStatus("run", { key: "whatif.error.replayNeedsPrefs", error: true });
      return;
    }
    const variants = collectVariants();
    if (!variants.length) {
      setStatus("run", { key: "whatif.error.noVariants", error: true });
      return;
    }

    closeStream();
    state.results = null;
    state.run = null;
    state.series = [];
    el("chart").innerHTML = "";
    el("chart-note").textContent = "";
    el("variant-legend").innerHTML = "";
    el("results-badge").textContent = "";
    el("metrics-table").querySelector("thead").innerHTML = "";
    el("metrics-table").querySelector("tbody").innerHTML = "";
    el("metrics-note").textContent = "";
    el("not-reproduced-list").innerHTML = "";
    showResults();
    setBusy(true);
    setStatus("run", { key: "whatif.status.running" });

    try {
      const response = await fetch("/api/whatif/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trace_id: state.traceId,
          mode: state.mode,
          variants,
          options: { cadence_min: state.options?.defaults?.cadence_min || 5 },
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      openStream(data.run_id);
    } catch (error) {
      setBusy(false);
      setStatus("run", {
        raw: error instanceof TypeError ? t("whatif.error.server") : error.message,
        error: true,
      });
    }
  }

  function openStream(runId) {
    const source = new EventSource(`/api/whatif/stream?id=${encodeURIComponent(runId)}`);
    state.source = source;
    source.onmessage = (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      handleFrame(frame);
    };
    source.onerror = () => {
      if (state.source !== source) return;
      closeStream();
      setBusy(false);
      setStatus("run", { key: "whatif.error.stream", error: true });
    };
  }

  function handleFrame(frame) {
    if (frame.type === "start") {
      state.run = {
        mode: frame.mode,
        windowStart: frame.window_start,
        windowEnd: frame.window_end,
        cadence: frame.cadence_min || 5,
        prefsLoaded: Boolean(frame.prefs_loaded),
        variantInfo: (frame.variants || []).map((label) => ({ label })),
      };
      state.series = (frame.variants || []).map(() => []);
      state.results = null;
      state.real = null;
      state.realInsulin = Array.isArray(frame.real_insulin)
        ? frame.real_insulin.map((event) => [Number(event[0]), Number(event[1]), String(event[2])])
        : [];
      if (Array.isArray(frame.real) && frame.real.length) {
        state.realSeries = frame.real.map((point) => [Number(point[0]), Number(point[1])]);
        state.realTrace = state.traceId;
      }
      const badge = el("results-badge");
      badge.textContent = t(
        frame.mode === "replay" ? "whatif.results.replay" : "whatif.results.model",
      );
      badge.classList.toggle("warn", frame.mode === "model");
      el("legend-model").classList.toggle("hidden", frame.mode !== "model");
      renderVariantLegend();
      scheduleDraw();
      return;
    }
    if (frame.type === "variant") {
      if (state.run && state.run.variantInfo[frame.index]) {
        state.run.variantInfo[frame.index] = {
          label: frame.label,
          algorithm: frame.algorithm,
          smb: frame.smb,
        };
      }
      renderVariantLegend();
      setStatus("run", { key: "whatif.status.variant", vars: { label: frame.label } });
      return;
    }
    if (frame.type === "cycle") {
      const series = state.series[frame.variant];
      if (!series) return;
      series.push({
        ts: frame.ts,
        glucose: frame.glucose,
        rate: frame.rate,
        smb: frame.smb,
        iob: frame.iob,
      });
      scheduleDraw();
      return;
    }
    if (frame.type === "done") {
      state.results = frame.results || [];
      state.real = frame.real || null;
      if (state.run && state.run.mode === "replay" && state.series[0]?.length && !state.realSeries) {
        state.realSeries = state.series[0].map((cycle) => [cycle.ts, cycle.glucose]);
        state.realTrace = state.traceId;
      }
      closeStream();
      setBusy(false);
      setStatus("run", { key: "whatif.status.done", ok: true });
      renderMetrics();
      renderNotReproduced();
      drawChart();
      return;
    }
    if (frame.type === "error") {
      closeStream();
      setBusy(false);
      setStatus("run", { raw: frame.message, error: true });
    }
  }

  function scheduleDraw() {
    if (state.drawQueued) return;
    state.drawQueued = true;
    requestAnimationFrame(() => {
      state.drawQueued = false;
      drawChart();
    });
  }

  function renderVariantLegend() {
    const box = el("variant-legend");
    box.innerHTML = "";
    const info = state.run?.variantInfo || state.variants;
    info.forEach((variant, index) => {
      const item = document.createElement("span");
      const dot = document.createElement("i");
      dot.className = "swatch";
      dot.style.background = color(index);
      const label = document.createElement("span");
      label.textContent = variant.label || `V${index + 1}`;
      item.append(dot, label);
      box.appendChild(item);
    });
  }

  function renderMetrics() {
    const table = el("metrics-table");
    const head = table.querySelector("thead");
    const body = table.querySelector("tbody");
    head.innerHTML = "";
    body.innerHTML = "";
    if (!state.results) return;
    const model = state.run?.mode === "model";
    const columns = [
      "variant", "algorithm", "smb", "cycles", "smbUnits", "smbCount",
      "manual", "basal", "total", "iobMean",
    ];
    if (model) {
      columns.push("mean", "tir", "tbr", "tbr54", "lbgi", "offset", "subject", "confidence");
    } else {
      columns.push("skipped");
    }
    const header = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("th");
      cell.textContent = t(`whatif.metrics.${column}`);
      header.appendChild(cell);
    });
    head.appendChild(header);

    const addCell = (row, content, cls) => {
      const cell = document.createElement("td");
      if (cls) cell.className = cls;
      cell.textContent = content;
      row.appendChild(cell);
      return cell;
    };
    const labelCell = (row, text, dot) => {
      const cell = document.createElement("td");
      if (dot) {
        const swatch = document.createElement("i");
        swatch.className = "swatch";
        swatch.style.background = dot;
        cell.append(swatch);
      }
      const label = document.createElement("span");
      label.textContent = text;
      cell.append(label);
      row.appendChild(cell);
    };

    if (state.real) {
      const real = state.real;
      const row = document.createElement("tr");
      row.className = "real-row";
      labelCell(row, t("whatif.metrics.realDay"));
      addCell(row, t("whatif.metrics.na"));
      addCell(row, t("whatif.metrics.na"));
      addCell(row, t("whatif.metrics.na"));
      addCell(row, fmt(real.smb_units, 2));
      addCell(row, fmt(real.smb_count, 0));
      addCell(row, fmt(real.manual_units, 2));
      addCell(row, fmt(real.basal_units, 2));
      addCell(row, fmt(Number(real.manual_units) + Number(real.smb_units) + Number(real.basal_units), 2));
      addCell(row, t("whatif.metrics.na"));
      columns.slice(10).forEach(() => addCell(row, t("whatif.metrics.na")));
      body.appendChild(row);
    }

    state.results.forEach((result, index) => {
      const metrics = result.metrics || {};
      const match = metrics.match || {};
      const row = document.createElement("tr");
      labelCell(row, result.label, color(index));
      addCell(row, t(`whatif.algorithm.${result.algorithm}`));
      addCell(row, result.smb ? t("whatif.metrics.on") : t("whatif.metrics.off"));
      addCell(row, fmt(metrics.cycles, 0));
      addCell(row, fmt(metrics.smb_units, 2));
      addCell(row, fmt(metrics.smb_count, 0));
      addCell(row, fmt(metrics.manual_units, 2));
      addCell(row, fmt(metrics.basal_units, 2));
      addCell(row, fmt(metrics.total_units, 2));
      addCell(row, fmt(metrics.iob_mean, 2));
      if (model) {
        addCell(row, fmt(metrics.mean_glucose, 1));
        addCell(row, pct(metrics.tir_70_180), metrics.tir_70_180 >= 0.7 ? "ok" : "");
        addCell(row, pct(metrics.tbr_70), metrics.tbr_70 > 0.04 ? "bad" : "");
        addCell(row, pct(metrics.tbr_54), metrics.tbr_54 > 0.01 ? "bad" : "");
        addCell(row, fmt(metrics.lbgi, 2));
        addCell(row, fmt(metrics.glucose_offset, 1));
        addCell(row, match.subject_name || t("whatif.metrics.na"));
        addCell(row, confidenceLabel(match.confidence));
      } else {
        addCell(row, fmt(metrics.skipped_cycles, 0));
      }
      body.appendChild(row);
    });

    const note = el("metrics-note");
    if (model) note.textContent = t("whatif.metrics.modelNote");
    else note.textContent = t("whatif.metrics.noTir");
  }

  function renderNotReproduced() {
    const list = el("not-reproduced-list");
    list.innerHTML = "";
    if (!state.summary && !state.results) return;
    const add = (text, cls) => {
      const item = document.createElement("li");
      if (cls) item.className = cls;
      item.textContent = text;
      list.appendChild(item);
    };
    const model = state.run?.mode === "model";
    if (model) {
      add(t("whatif.notReproduced.model"), "static");
      add(t("whatif.notReproduced.modelAbsolute"), "static");
    } else {
      add(t("whatif.notReproduced.tir"), "static");
      add(t("whatif.notReproduced.parity"), "static");
    }
    add(t("whatif.notReproduced.exercise"), "static");
    add(t("whatif.notReproduced.unknown"), "static");
    if (model && (state.results || []).some(
      (result) => (result.metrics?.match?.confidence || "good") !== "good",
    )) {
      add(t("whatif.notReproduced.modelLow"), "static");
    }

    const seen = new Set();
    const warnings = [];
    (state.summary?.warnings || []).forEach((warning) => warnings.push(warning));
    (state.results || []).forEach((result) => {
      (result.warnings || []).forEach((warning) => warnings.push(warning));
    });
    warnings.forEach((warning) => {
      const text = String(warning);
      const key = text.replace(/^trace:\s*/i, "");
      if (seen.has(key)) return;
      seen.add(key);
      add(text, "server");
    });
    if (!list.children.length) add(t("whatif.notReproduced.none"));
  }

  function drawChart() {
    const svg = el("chart");
    const run = state.run;
    if (!run || !state.series.length) {
      svg.innerHTML = "";
      return;
    }
    let start = Number(run.windowStart);
    let end = Number(run.windowEnd);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      const stamps = state.series.flat().map((cycle) => cycle.ts);
      if (!stamps.length) {
        svg.innerHTML = "";
        return;
      }
      start = Math.min(...stamps);
      end = Math.max(...stamps);
    }
    const span = Math.max(1, end - start);
    const model = run.mode === "model";
    const W = 900;
    const H = 430;
    const padL = 50;
    const padR = 14;
    const plotW = W - padL - padR;
    const x = (ts) => padL + ((Number(ts) - start) / span) * plotW;

    const gTop = 20;
    const gBottom = 244;
    const iTop = 270;
    const iBottom = 342;
    const oTop = 366;
    const oBottom = 414;

    let gMax = 180;
    let gMin = 70;
    const allGlucose = [];
    state.series.forEach((series) => series.forEach((cycle) => {
      if (Number.isFinite(Number(cycle.glucose))) allGlucose.push(Number(cycle.glucose));
    }));
    const realAvailable = Boolean(
      (state.realSeries && state.realTrace === state.traceId)
      || (!model && state.series[0]?.length),
    );
    const realSeries = state.realSeries && state.realTrace === state.traceId
      ? state.realSeries
      : model ? null : state.series[0];
    if (realSeries) {
      realSeries.forEach((point) => {
        const value = Array.isArray(point) ? Number(point[1]) : Number(point.glucose);
        if (Number.isFinite(value)) allGlucose.push(value);
      });
    }
    allGlucose.forEach((value) => {
      gMax = Math.max(gMax, value);
      gMin = Math.min(gMin, value);
    });
    const hi = Math.max(300, Math.ceil(gMax / 50) * 50);
    const lo = Math.min(40, Math.floor(gMin / 20) * 20);
    const yG = (value) => gBottom - ((value - lo) / (hi - lo)) * (gBottom - gTop);

    const insulinBuckets = Math.max(1, Math.ceil(span / 3_600_000));
    const buckets = state.series.map(() => Array.from(
      { length: insulinBuckets },
      () => ({ basal: 0, smb: 0 }),
    ));
    const realBuckets = Array.from({ length: insulinBuckets }, () => 0);
    state.realInsulin.forEach((event) => {
      const bucket = Math.floor((Number(event[0]) - start) / 3_600_000);
      if (bucket < 0 || bucket >= insulinBuckets) return;
      realBuckets[bucket] += Number(event[1]) || 0;
    });
    state.series.forEach((series, index) => series.forEach((cycle, cycleIndex) => {
      const bucket = Math.floor((Number(cycle.ts) - start) / 3_600_000);
      if (bucket < 0 || bucket >= insulinBuckets) return;
      const following = cycleIndex + 1 < series.length
        ? Number(series[cycleIndex + 1].ts)
        : Number(run.windowEnd);
      const cycleStart = Number(cycle.ts);
      const cycleEnd = Number.isFinite(following)
        ? Math.max(cycleStart, Math.min(Number(run.windowEnd) || following, following))
        : cycleStart;
      const basal = (Number(cycle.rate) || 0) * (cycleEnd - cycleStart) / 3_600_000;
      buckets[index][bucket].basal += basal;
      buckets[index][bucket].smb += Number(cycle.smb) || 0;
    }));
    let insulinMax = 1;
    buckets.forEach((variant) => variant.forEach((bucket) => {
      insulinMax = Math.max(insulinMax, bucket.basal + bucket.smb);
    }));
    realBuckets.forEach((units) => {
      insulinMax = Math.max(insulinMax, units);
    });
    insulinMax = Math.ceil(insulinMax);
    const yI = (value) => iBottom - (value / insulinMax) * (iBottom - iTop);

    let iobMax = 4;
    state.series.forEach((series) => series.forEach((cycle) => {
      if (Number.isFinite(Number(cycle.iob))) iobMax = Math.max(iobMax, Number(cycle.iob));
    }));
    iobMax = Math.ceil(iobMax / 2) * 2;
    const yO = (value) => oBottom - (value / iobMax) * (oBottom - oTop);

    const parts = [];
    parts.push(`<rect x="${padL}" y="${yG(180).toFixed(1)}" width="${plotW}" height="${(yG(70) - yG(180)).toFixed(1)}" fill="rgba(102,187,106,0.10)"/>`);
    for (let value = Math.ceil(lo / 50) * 50; value <= hi; value += 50) {
      const y = yG(value).toFixed(1);
      parts.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.07)"/>`);
      parts.push(`<text x="${padL - 8}" y="${Number(y) + 4}" text-anchor="end" fill="#8a97a3" font-size="10">${value}</text>`);
    }
    const tickHours = span > 2 * 86_400_000 ? 12 : span > 86_400_000 ? 6 : 3;
    for (let ts = start; ts <= end; ts += tickHours * 3_600_000) {
      const tickX = x(ts).toFixed(1);
      parts.push(`<line x1="${tickX}" y1="${gTop}" x2="${tickX}" y2="${oBottom}" stroke="rgba(255,255,255,0.05)"/>`);
      const date = new Date(ts);
      const text = date.getHours() === 0
        ? date.toLocaleDateString(locale(), { day: "2-digit", month: "2-digit" })
        : date.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
      parts.push(`<text x="${tickX}" y="${oBottom + 14}" text-anchor="middle" fill="#8a97a3" font-size="9">${escapeXml(text)}</text>`);
    }

    const path = (points) => {
      const clean = points.filter(
        (point) => Number.isFinite(point[0]) && Number.isFinite(point[1]),
      );
      return clean
        .map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(1)} ${point[1].toFixed(1)}`)
        .join(" ");
    };

    if (realSeries) {
      const points = realSeries.map((point) => {
        const ts = Array.isArray(point) ? point[0] : point.ts;
        const value = Array.isArray(point) ? point[1] : point.glucose;
        return [x(ts), yG(Number(value))];
      });
      parts.push(`<path d="${path(points)}" fill="none" stroke="${REAL_COLOR}" stroke-width="1.6" stroke-linejoin="round"/>`);
    }
    if (model) {
      state.series.forEach((series, index) => {
        const points = series.map((cycle) => [x(cycle.ts), yG(Number(cycle.glucose))]);
        parts.push(`<path d="${path(points)}" fill="none" stroke="${color(index)}" stroke-width="1.4" stroke-linejoin="round"/>`);
      });
    }

    const groupWidth = plotW / insulinBuckets;
    const realBars = state.realInsulin.length > 0;
    const barSlots = state.series.length + (realBars ? 1 : 0);
    const barWidth = Math.max(1, (groupWidth * 0.86) / Math.max(1, barSlots));
    realBuckets.forEach((units, bucketIndex) => {
      if (units <= 0) return;
      const base = padL + bucketIndex * groupWidth + groupWidth * 0.07;
      const top = yI(units);
      parts.push(`<rect x="${base.toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1, barWidth - 1).toFixed(1)}" height="${(iBottom - top).toFixed(1)}" fill="${REAL_BOLUS_COLOR}" opacity="0.55"/>`);
    });
    buckets.forEach((variant, index) => {
      const offset = (index + (realBars ? 1 : 0)) * barWidth;
      variant.forEach((bucket, bucketIndex) => {
        const base = padL + bucketIndex * groupWidth + groupWidth * 0.07 + offset;
        const total = bucket.basal + bucket.smb;
        if (total > 0) {
          const top = yI(total);
          parts.push(`<rect x="${base.toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1, barWidth - 1).toFixed(1)}" height="${(iBottom - top).toFixed(1)}" fill="${color(index)}" opacity="0.35"/>`);
        }
        if (bucket.smb > 0) {
          const top = yI(bucket.smb);
          parts.push(`<rect x="${base.toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1, barWidth - 1).toFixed(1)}" height="${(iBottom - top).toFixed(1)}" fill="${color(index)}" opacity="0.95"/>`);
        }
      });
    });
    parts.push(`<line x1="${padL}" y1="${iBottom}" x2="${W - padR}" y2="${iBottom}" stroke="rgba(255,255,255,0.12)"/>`);
    parts.push(`<text x="${padL - 8}" y="${iTop + 4}" text-anchor="end" fill="#8a97a3" font-size="10">${insulinMax}</text>`);
    parts.push(`<text x="${padL - 8}" y="${iBottom + 4}" text-anchor="end" fill="#8a97a3" font-size="10">0</text>`);

    state.series.forEach((series, index) => {
      const points = series.map((cycle) => [x(cycle.ts), yO(Number(cycle.iob) || 0)]);
      parts.push(`<path d="${path(points)}" fill="none" stroke="${color(index)}" stroke-width="1.5" stroke-linejoin="round"/>`);
    });
    parts.push(`<line x1="${padL}" y1="${oBottom}" x2="${W - padR}" y2="${oBottom}" stroke="rgba(255,255,255,0.12)"/>`);
    parts.push(`<line x1="${padL}" y1="${yO(iobMax / 2).toFixed(1)}" x2="${W - padR}" y2="${yO(iobMax / 2).toFixed(1)}" stroke="rgba(255,255,255,0.07)"/>`);
    parts.push(`<text x="${padL - 8}" y="${oTop + 4}" text-anchor="end" fill="#8a97a3" font-size="10">${iobMax}</text>`);
    parts.push(`<text x="${padL - 8}" y="${(yO(iobMax / 2) + 4).toFixed(1)}" text-anchor="end" fill="#8a97a3" font-size="10">${iobMax / 2}</text>`);
    parts.push(`<text x="${padL - 8}" y="${oBottom + 4}" text-anchor="end" fill="#8a97a3" font-size="10">0</text>`);

    parts.push(`<text x="${padL + 4}" y="${gTop + 12}" fill="#8a97a3" font-size="10">${escapeXml(t("whatif.chart.glucose"))}</text>`);
    parts.push(`<text x="${padL + 4}" y="${iTop + 12}" fill="#8a97a3" font-size="10">${escapeXml(t("whatif.chart.insulin"))}</text>`);
    parts.push(`<text x="${padL + 4}" y="${oTop + 12}" fill="#8a97a3" font-size="10">${escapeXml(t("whatif.chart.iob"))}</text>`);

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join("");
    const chartNotes = [];
    if (model && !realAvailable) chartNotes.push(t("whatif.chart.noReal"));
    if (model) chartNotes.push(t("whatif.notReproduced.modelAbsolute"));
    el("chart-note").textContent = chartNotes.join(" ");
  }

  function onLanguageChange() {
    el("footer-lang").textContent = String(window.SIM_LANG || "en").toUpperCase();
    el("chart").setAttribute("aria-label", t("whatif.chart.title"));
    renderSummary();
    renderModeNote();
    repaintStatuses();
    renderVariantLegend();
    renderMetrics();
    renderNotReproduced();
    drawChart();
  }

  function init() {
    state.variants = [newVariant(0), { ...newVariant(1), algorithm: "off" }];
    renderVariants();
    renderModeNote();
    el("footer-lang").textContent = String(window.SIM_LANG || "en").toUpperCase();
    el("chart").setAttribute("aria-label", t("whatif.chart.title"));
    window.applyI18n?.();

    document.querySelectorAll('input[name="mode"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        if (!radio.checked) return;
        state.mode = radio.value;
        renderModeNote();
      });
    });
    el("variant-list").addEventListener("input", onVariantInput);
    el("variant-list").addEventListener("change", onVariantInput);
    el("variant-list").addEventListener("click", onVariantClick);
    el("connect-form").addEventListener("submit", connect);
    el("delete-import").addEventListener("click", deleteImport);
    el("run").addEventListener("click", startRun);
    el("add-variant").addEventListener("click", () => {
      if (state.busy || state.variants.length >= maxVariants()) return;
      state.variants.push(newVariant(state.variants.length));
      renderVariants();
    });
    window.addEventListener("sim-lang", onLanguageChange);
    loadOptions();

    if (window.location.protocol === "file:") {
      setStatus("connect", { key: "whatif.error.server", error: true });
    }
  }

  init();
})();
