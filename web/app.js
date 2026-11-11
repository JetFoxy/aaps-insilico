(() => {
  "use strict";

  const payload = window.SIM_RUNS;
  if (!payload || !payload.runs) {
    document.body.innerHTML = "<p style='padding:24px'>data/runs.js не найден. Запустите <code>python/runner/export_demo.py</code>.</p>";
    return;
  }

  const runs = payload.runs;
  window.applyI18n();
  const el = (id) => document.getElementById(id);
  const PAD = { l: 46, r: 16, t: 16, b: 26 };
  const COLORS = {
    cgm: "#4fc3f7", plasma: "#90a4ae", iob: "#ab47bc", rate: "#ffb74d",
    smb: "#ffd54f", good: "#66bb6a", bad: "#ef5350",
    grid: "rgba(255,255,255,0.07)", text: "#8a97a3",
  };

  const state = {
    run: null, upto: 1, playing: false, speed: 4, hover: null, lastTs: 0, acc: 0,
    compare: false, live: false, source: null, hasServer: false,
  };

  let baseSubjects = [...new Set(runs.map((r) => r.subject))];
  const scenarios = (subject) => [...new Set(runs.filter((r) => r.subject === subject).map((r) => r.scenario))];
  const arms = (subject, scenario) => runs
    .filter((r) => r.subject === subject && r.scenario === scenario)
    .map((r) => r.arm);

  const scenarioLabel = (id) => t(`scenario.${id}`);

  function fmtClock(min) {
    const h = Math.floor(min / 60);
    const m = Math.round(min % 60);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }

  function setupCanvas(canvas, cssHeight) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || canvas.parentElement.clientWidth - 24;
    canvas.style.height = cssHeight + "px";
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h: cssHeight };
  }

  function xFor(t, w, duration) {
    return PAD.l + (t / duration) * (w - PAD.l - PAD.r);
  }

  function line(ctx, pts, color, width, dash) {
    if (pts.length < 2) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
    ctx.restore();
  }

  function axisY(ctx, w, h, min, max, step, format) {
    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.text;
    ctx.font = "11px -apple-system, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let v = min; v <= max + 1e-9; v += step) {
      const y = PAD.t + (1 - (v - min) / (max - min)) * (h - PAD.t - PAD.b);
      ctx.beginPath();
      ctx.moveTo(PAD.l, y);
      ctx.lineTo(w - PAD.r, y);
      ctx.stroke();
      ctx.fillText(format(v), PAD.l - 8, y);
    }
    ctx.restore();
  }

  function axisX(ctx, w, h, duration) {
    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.text;
    ctx.font = "11px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const stepH = duration > 720 ? 3 : 1;
    for (let m = 0; m <= duration; m += stepH * 60) {
      const x = xFor(m, w, duration);
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, h - PAD.b);
      ctx.stroke();
      ctx.fillText(fmtClock(m), x, h - PAD.b + 6);
    }
    ctx.restore();
  }

  function compareRun() {
    if (!state.compare || !state.run) return null;
    const other = state.run.arm === "on" ? "off" : "on";
    return runs.find((r) => r.subject === state.run.subject && r.scenario === state.run.scenario && r.arm === other) || null;
  }

  function drawGlucose() {
    const run = state.run;
    const cmp = compareRun();
    const canvas = el("glucose");
    const { ctx, w, h } = setupCanvas(canvas, 320);
    const n = Math.min(state.upto, run.time.length);
    const duration = run.duration_min;
    const values = run.cgm.concat(run.plasma).concat(cmp ? cmp.cgm : []);
    const hi = Math.max(300, Math.ceil(Math.max(...values) / 50) * 50);
    const lo = 40;
    const y = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (h - PAD.t - PAD.b);

    ctx.clearRect(0, 0, w, h);

    ctx.fillStyle = "rgba(102,187,106,0.10)";
    ctx.fillRect(PAD.l, y(180), w - PAD.l - PAD.r, y(70) - y(180));

    axisY(ctx, w, h, lo, hi, 50, (v) => String(v));
    axisX(ctx, w, h, duration);

    ctx.save();
    ctx.strokeStyle = "rgba(239,83,80,0.55)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(PAD.l, y(70));
    ctx.lineTo(w - PAD.r, y(70));
    ctx.stroke();
    ctx.restore();

    const cgm = [], plasma = [];
    for (let i = 0; i < n; i++) {
      const x = xFor(run.time[i], w, duration);
      cgm.push([x, y(run.cgm[i])]);
      plasma.push([x, y(run.plasma[i])]);
    }
    if (cmp) {
      const cmpPts = [];
      const m = Math.min(n, cmp.time.length);
      for (let i = 0; i < m; i++) cmpPts.push([xFor(cmp.time[i], w, duration), y(cmp.cgm[i])]);
      line(ctx, cmpPts, COLORS.good, 1.5, [6, 4]);
    }
    line(ctx, plasma, COLORS.plasma, 1.2, [4, 3]);
    line(ctx, cgm, COLORS.cgm, 1.8);

    ctx.save();
    ctx.fillStyle = COLORS.smb;
    for (const [t, units] of run.smb) {
      if (t > (run.time[n - 1] || 0)) break;
      const x = xFor(t, w, duration);
      ctx.beginPath();
      ctx.arc(x, PAD.t + 6, Math.min(5, 1.5 + units * 0.7), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    for (const [t] of run.meals) {
      if (t > (run.time[n - 1] || 0)) break;
      const x = xFor(t, w, duration);
      ctx.beginPath();
      ctx.moveTo(x, h - PAD.b - 4);
      ctx.lineTo(x - 5, h - PAD.b);
      ctx.lineTo(x + 5, h - PAD.b);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();

    const cursor = state.hover != null ? state.hover : n - 1;
    if (cursor >= 0 && cursor < run.time.length) {
      const x = xFor(run.time[cursor], w, duration);
      ctx.save();
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.beginPath();
      ctx.moveTo(x, PAD.t);
      ctx.lineTo(x, h - PAD.b);
      ctx.stroke();
      ctx.fillStyle = COLORS.cgm;
      ctx.beginPath();
      ctx.arc(x, y(run.cgm[cursor]), 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

  }

  function drawInsulin() {
    const run = state.run;
    const canvas = el("insulin");
    const { ctx, w, h } = setupCanvas(canvas, 190);
    const n = Math.min(state.upto, run.time.length);
    const duration = run.duration_min;
    const iobMax = Math.max(4, Math.ceil(Math.max(...run.iob.slice(0, n)) + 1));
    const rateMax = Math.max(4, Math.ceil(Math.max(...run.rate.slice(0, n)) + 1));
    const yIob = (v) => PAD.t + (1 - v / iobMax) * (h - PAD.t - PAD.b);
    const yRate = (v) => PAD.t + (1 - v / rateMax) * (h - PAD.t - PAD.b);

    ctx.clearRect(0, 0, w, h);
    axisY(ctx, w, h, 0, iobMax, 2, (v) => String(v));
    axisX(ctx, w, h, duration);

    const iobPts = [];
    for (let i = 0; i < n; i++) iobPts.push([xFor(run.time[i], w, duration), yIob(run.iob[i])]);
    if (iobPts.length > 1) {
      ctx.save();
      ctx.fillStyle = "rgba(171,71,188,0.16)";
      ctx.beginPath();
      ctx.moveTo(iobPts[0][0], h - PAD.b);
      iobPts.forEach(([x, y]) => ctx.lineTo(x, y));
      ctx.lineTo(iobPts[iobPts.length - 1][0], h - PAD.b);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
    line(ctx, iobPts, COLORS.iob, 1.8);

    const ratePts = [];
    for (let i = 0; i < n; i++) {
      const x = xFor(run.time[i], w, duration);
      const y = yRate(run.rate[i]);
      if (ratePts.length) ratePts.push([x, ratePts[ratePts.length - 1][1]]);
      ratePts.push([x, y]);
    }
    line(ctx, ratePts, COLORS.rate, 1.4);

    ctx.save();
    ctx.font = "11px -apple-system, sans-serif";
    ctx.textAlign = "right";
    ctx.fillStyle = COLORS.rate;
    ctx.fillText(t("chart.rate"), w - PAD.r, PAD.t + 12);
    ctx.textAlign = "left";
    ctx.fillStyle = COLORS.iob;
    ctx.fillText(t("chart.iob"), PAD.l + 4, PAD.t + 12);
    ctx.restore();
  }

  function drawDeltas() {
    const box = el("deltas");
    const caption = el("delta-caption");
    el("cmp-legend").classList.toggle("hidden", !state.compare);
    if (!state.compare || !state.run) { box.innerHTML = ""; caption.classList.add("hidden"); return; }
    const on = runs.find((r) => r.subject === state.run.subject && r.scenario === state.run.scenario && r.arm === "on");
    const off = runs.find((r) => r.subject === state.run.subject && r.scenario === state.run.scenario && r.arm === "off");
    if (!on || !off) { box.innerHTML = ""; caption.classList.add("hidden"); return; }
    caption.classList.remove("hidden");
    const a = on.metrics;
    const b = off.metrics;
    const ru = window.SIM_LANG === "ru";
    const rows = [
      [t("delta.tir"), (a.tir_70_180 - b.tir_70_180) * 100, "pp", true],
      [t("delta.tbr"), (a.tbr_70 - b.tbr_70) * 100, "pp", false],
      [t("delta.lbgi"), a.lbgi - b.lbgi, "", false],
      [t("delta.dose"), a.total_insulin - b.total_insulin, ru ? "ЕД" : "U", false],
      [t("delta.smb"), a.smb_count - b.smb_count, "", false],
      [t("delta.falling"), (a.insulin_while_falling_fraction - b.insulin_while_falling_fraction) * 100, ru ? "п.п." : "pp", false],
    ];
    box.innerHTML = rows.map(([k, v, unit, higherBetter]) => {
      const good = higherBetter ? v > 0 : v < 0;
      const cls = Math.abs(v) < 1e-9 ? "" : good ? "good" : "bad";
      const text = (v > 0 ? "+" : "") + v.toFixed(unit === "pp" || unit === "п.п." ? 1 : 2) + (unit ? " " + unit : "");
      return `<div class="card"><div class="k">${k}</div><div class="v ${cls}">${text}</div></div>`;
    }).join("");
  }

  function liveMetrics(run) {
    const n = run.cgm.length;
    if (!n) return { tir_70_180: 0, tbr_70: 0, lbgi: 0, mean_glucose: 0, total_insulin: 0, smb_count: 0 };
    let inRange = 0, low = 0, sum = 0, dose = 0;
    for (let i = 0; i < n; i++) {
      const g = run.cgm[i];
      sum += g;
      if (g >= 70 && g <= 180) inRange += 1;
      if (g < 70) low += 1;
      dose += (run.rate[i] * run.cadence_min) / 60;
    }
    for (const [, units] of run.smb) dose += units;
    return {
      tir_70_180: inRange / n, tbr_70: low / n, lbgi: 0,
      mean_glucose: sum / n, total_insulin: dose, smb_count: run.smb.length,
    };
  }

  function currentArm() {
    const active = document.querySelector(".arm.active");
    return active ? active.dataset.arm : "off";
  }

  function stopLive() {
    if (state.source) { state.source.close(); state.source = null; }
    state.live = false;
    el("live").classList.remove("on");
    el("live").textContent = t("ctl.live");
    el("play").disabled = false;
    el("scrub").disabled = false;
    el("compare").disabled = false;
  }

  function handleFrame(frame) {
    if (frame.type === "start") {
      state.run = {
        id: "live", subject: frame.subject, scenario: frame.scenario, arm: frame.arm,
        cadence_min: frame.cadence_min, duration_min: frame.duration_min,
        meals: frame.meals || [], time: [], cgm: [], plasma: [], iob: [], cob: [],
        rate: [], sens_ratio: [], smb: [], reason: [], metrics: {},
      };
      state.upto = 0;
      el("scrub").max = "0";
      const p = frame.params || {};
      el("meta").textContent =
        `live · ${frame.subject} · ${frame.scenario} · ${frame.arm} · ` +
        `шум ${p.noise}×${p.noise_scale} · когорта ×${p.cohort_copies} · ISF ${p.isf_source} · темпы ${p.temp_mode}`;
      return;
    }
    if (frame.type === "cycle" && state.run) {
      const run = state.run;
      run.time.push(frame.time);
      run.cgm.push(frame.cgm);
      run.plasma.push(frame.plasma);
      run.iob.push(frame.iob);
      run.cob.push(frame.cob);
      run.rate.push(frame.rate);
      run.sens_ratio.push(frame.sens_ratio);
      run.reason.push(frame.reason);
      if (frame.smb > 0) run.smb.push([frame.time, frame.smb]);
      state.upto = run.time.length;
      run.metrics = liveMetrics(run);
      el("scrub").max = String(Math.max(0, run.time.length - 1));
      drawCards();
      draw();
      return;
    }
    if (frame.type === "done" && state.run) {
      state.run.metrics = frame.metrics;
      drawCards();
      draw();
      stopLive();
      return;
    }
    if (frame.type === "error") {
      el("reason-text").textContent = t("live.error", { msg: frame.message });
      stopLive();
    }
  }

  function startLive() {
    if (window.location.protocol === "file:") {
      el("reason-text").textContent = t("live.file");
      return;
    }
    stopLive();
    state.playing = false;
    el("play").textContent = "▶";
    el("live").classList.add("on");
    el("live").textContent = t("ctl.live.stop");
    el("play").disabled = true;
    el("scrub").disabled = true;
    el("compare").disabled = true;
    state.live = true;
    const body = {
      subject: el("subject").value,
      scenario: el("scenario").value,
      arm: currentArm(),
      speed: Number(el("speed").value),
      params: paramsFromUI(),
    };
    fetch("/api/start", { method: "POST", body: JSON.stringify(body) })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ id }) => {
        state.source = new EventSource(`/api/stream?id=${id}`);
        state.source.onmessage = (event) => handleFrame(JSON.parse(event.data));
        state.source.onerror = () => {
          if (state.live) el("reason-text").textContent = t("live.interrupted");
          stopLive();
        };
      })
      .catch((err) => {
        el("reason-text").textContent = t("live.unavailable", { msg: err.message });
        stopLive();
      });
  }

  function drawCards() {
    const m = state.run.metrics;
    const cards = [
      [t("card.tir"), (m.tir_70_180 * 100).toFixed(1) + " %", m.tir_70_180 >= 0.7 ? "good" : "bad"],
      [t("card.tbr"), (m.tbr_70 * 100).toFixed(1) + " %", m.tbr_70 > 0.04 ? "bad" : "good"],
      [t("card.lbgi"), m.lbgi.toFixed(2), m.lbgi > 2.5 ? "bad" : ""],
      [t("card.mean"), Math.round(m.mean_glucose) + (window.SIM_LANG === "ru" ? " мг/дл" : " mg/dL"), ""],
      [t("card.dose"), m.total_insulin.toFixed(1) + (window.SIM_LANG === "ru" ? " ЕД" : " U"), ""],
      [t("card.smb"), String(m.smb_count), ""],
    ];
    el("cards").innerHTML = cards.map(([k, v, cls]) =>
      `<div class="card"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`).join("");
  }

  function currentIndex() {
    return state.hover != null ? state.hover : Math.min(state.upto - 1, state.run.time.length - 1);
  }

  function drawReason() {
    const i = currentIndex();
    const text = i >= 0 ? state.run.reason[i] || "—" : "—";
    el("reason-text").textContent = text;
    const run = state.run;
    if (run.arm === "off") {
      el("autoisf").textContent = t("autoisf.off");
      return;
    }
    const values = run.sens_ratio;
    if (!values.length) {
      el("autoisf").textContent = t("autoisf.none");
      return;
    }
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const changed = values.filter((v) => Math.abs(v - 1) > 1e-6).length / values.length;
    el("autoisf").textContent = t("autoisf.summary", {
      mean: mean.toFixed(3), min: min.toFixed(2), max: max.toFixed(2),
      pct: (changed * 100).toFixed(0),
    });
  }

  function draw() {
    if (!state.run) return;
    drawGlucose();
    drawInsulin();
    drawReason();
    const i = currentIndex();
    el("clock").textContent = i >= 0 ? fmtClock(state.run.time[i]) : "00:00";
    el("scrub").value = String(Math.max(0, state.upto - 1));
  }

  function selectRun() {
    const subject = el("subject").value;
    const scenario = el("scenario").value;
    const available = arms(subject, scenario);
    let arm = "off";
    if (!available.includes(arm)) arm = available[0];
    const active = document.querySelector(".arm.active");
    if (active && available.includes(active.dataset.arm)) arm = active.dataset.arm;
    state.run = runs.find((r) => r.subject === subject && r.scenario === scenario && r.arm === arm);
    state.upto = state.run.time.length;
    state.hover = null;
    state.playing = false;
    el("play").textContent = "▶";
    el("scrub").max = String(state.run.time.length - 1);
    el("scrub").value = String(state.run.time.length - 1);
    document.querySelectorAll(".arm").forEach((b) => {
      b.disabled = !available.includes(b.dataset.arm);
      b.classList.toggle("active", b.dataset.arm === arm);
    });
    drawCards();
    drawDeltas();
    draw();
  }

  function fillScenarios() {
    const subject = el("subject").value;
    const list = scenarios(subject);
    const previous = el("scenario").value;
    el("scenario").innerHTML = list
      .map((s) => `<option value="${s}">${scenarioLabel(s)}</option>`).join("");
    if (list.includes(previous)) el("scenario").value = previous;
  }

  function tick(ts) {
    if (!state.playing) return;
    if (!state.lastTs) state.lastTs = ts;
    const dt = ts - state.lastTs;
    state.lastTs = ts;
    state.acc += dt;
    const perCycle = 110 / state.speed;
    while (state.acc >= perCycle) {
      state.acc -= perCycle;
      if (state.upto < state.run.time.length) state.upto += 1;
      else { state.playing = false; el("play").textContent = "▶"; break; }
    }
    draw();
    if (state.playing) requestAnimationFrame(tick);
  }

  function fillSubjects() {
    const copies = state.hasServer ? Math.max(0, Number(el("p-copies").value) || 0) : 0;
    const list = [];
    for (const name of baseSubjects) {
      list.push(name);
      for (let index = 1; index <= copies; index++) {
        list.push(`${name}-v${String(index).padStart(2, "0")}`);
      }
    }
    list.sort();
    const previous = el("subject").value;
    el("subject").innerHTML = list.map((name) => `<option value="${name}">${name}</option>`).join("");
    if (list.includes(previous)) el("subject").value = previous;
  }

  function paramsFromUI() {
    const num = (id) => Number(el(id).value);
    return {
      cohort_copies: num("p-copies"),
      cohort_seed: num("p-seed"),
      noise: el("p-noise").value,
      noise_scale: num("p-noise-scale"),
      noise_sigma: num("p-noise-sigma"),
      noise_ar1: num("p-noise-ar1"),
      isf_source: el("p-isf").value,
      temp_mode: el("p-temp").value,
      variability: el("p-var").checked,
      smb_ratio_3min: num("p-smb"),
      prefs: {
        bgAccel_ISF_weight: num("p-w-accel"),
        bgBrake_ISF_weight: num("p-w-brake"),
        lower_ISFrange_weight: num("p-w-lower"),
        higher_ISFrange_weight: num("p-w-higher"),
        pp_ISF_weight: num("p-w-pp"),
        dura_ISF_weight: num("p-w-dura"),
        autoISF_min: num("p-isf-min"),
        autoISF_max: num("p-isf-max"),
      },
    };
  }

  function applyDefaults(defaults) {
    const set = (id, value) => {
      if (value === undefined || value === null) return;
      const node = el(id);
      if (node.type === "checkbox") node.checked = Boolean(value);
      else node.value = String(value);
    };
    set("p-copies", defaults.cohort_copies);
    set("p-seed", defaults.cohort_seed);
    set("p-noise", defaults.noise);
    set("p-noise-scale", defaults.noise_scale);
    set("p-noise-sigma", defaults.noise_sigma);
    set("p-noise-ar1", defaults.noise_ar1);
    set("p-isf", defaults.isf_source);
    set("p-temp", defaults.temp_mode);
    set("p-var", defaults.variability);
    set("p-smb", defaults.smb_ratio_3min);
  }

  fetch("/api/options")
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (!data) return;
      state.hasServer = true;
      if (Array.isArray(data.base_subjects) && data.base_subjects.length) {
        baseSubjects = data.base_subjects;
      }
      applyDefaults(data.defaults || {});
      fillSubjects();
      fillScenarios();
      selectRun();
    })
    .catch(() => {});

  fillSubjects();
  fillScenarios();

  const params = new URLSearchParams(window.location.search);
  if (params.get("params") === "1") el("params").open = true;
  if (params.get("compare") === "1") {
    state.compare = true;
    el("compare").checked = true;
  }
  const wanted = params.get("run");
  if (wanted) {
    const run = runs.find((r) => r.id === wanted);
    if (run) {
      el("subject").value = run.subject;
      fillScenarios();
      el("scenario").value = run.scenario;
      document.querySelectorAll(".arm").forEach((b) => b.classList.toggle("active", b.dataset.arm === run.arm));
    }
  }

  el("p-copies").addEventListener("change", () => {
    if (!state.hasServer) return;
    stopLive();
    fillSubjects();
    fillScenarios();
    selectRun();
  });

  el("subject").addEventListener("change", () => { stopLive(); fillScenarios(); selectRun(); });
  el("scenario").addEventListener("change", () => { stopLive(); selectRun(); });
  document.querySelectorAll(".arm").forEach((b) => b.addEventListener("click", () => {
    stopLive();
    document.querySelectorAll(".arm").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    selectRun();
  }));

  el("live").addEventListener("click", () => {
    if (state.live) stopLive();
    else startLive();
  });

  el("play").addEventListener("click", () => {
    if (state.upto >= state.run.time.length) state.upto = 1;
    state.playing = !state.playing;
    el("play").textContent = state.playing ? "❚❚" : "▶";
    if (state.playing) { state.lastTs = 0; state.acc = 0; requestAnimationFrame(tick); }
  });

  el("scrub").addEventListener("input", (e) => {
    state.playing = false;
    el("play").textContent = "▶";
    state.upto = Number(e.target.value) + 1;
    draw();
  });

  el("speed").addEventListener("change", (e) => { state.speed = Number(e.target.value); });

  el("compare").addEventListener("change", (e) => {
    state.compare = e.target.checked;
    drawDeltas();
    draw();
  });

  const glucoseCanvas = el("glucose");
  const tooltip = el("tooltip");
  glucoseCanvas.addEventListener("mousemove", (e) => {
    if (!state.run) return;
    const rect = glucoseCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const w = rect.width;
    const duration = state.run.duration_min;
    const t = ((x - PAD.l) / (w - PAD.l - PAD.r)) * duration;
    if (t < 0 || t > duration) { state.hover = null; tooltip.classList.add("hidden"); draw(); return; }
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < state.run.time.length; i++) {
      const d = Math.abs(state.run.time[i] - t);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    if (best >= state.upto) { state.hover = null; tooltip.classList.add("hidden"); draw(); return; }
    state.hover = best;
    const run = state.run;
    const ru = window.SIM_LANG === "ru";
    tooltip.innerHTML = `<b>${fmtClock(run.time[best])}</b> · CGM ${Math.round(run.cgm[best])} · ` +
      `${t("legend.plasma")} ${Math.round(run.plasma[best])}<br>` +
      `${t("tip.iob")} ${run.iob[best].toFixed(2)} ${ru ? "ЕД" : "U"} · ${t("tip.rate")} ${run.rate[best].toFixed(2)} U/h · ` +
      `ISF×${run.sens_ratio[best].toFixed(2)}` + (best < 2 ? ` (${t("tip.start")})` : "");
    tooltip.style.left = x + "px";
    tooltip.style.top = (e.clientY - rect.top) + "px";
    tooltip.classList.remove("hidden");
    draw();
  });
  glucoseCanvas.addEventListener("mouseleave", () => {
    state.hover = null;
    tooltip.classList.add("hidden");
    draw();
  });

  window.addEventListener("resize", () => draw());

  el("meta").textContent = `${payload.model} · ${runs.length} runs · ${payload.generated_at}`;
  selectRun();
  window.addEventListener("sim-lang", () => {
    fillScenarios();
    drawCards();
    drawDeltas();
    draw();
    if (!state.live) el("live").textContent = t("ctl.live");
  });

  if (params.get("live") === "1") startLive();
})();
