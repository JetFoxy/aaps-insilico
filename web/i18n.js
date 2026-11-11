(() => {
  "use strict";

  const DICT = {
    ru: {
      subtitle: "замкнутый контур: настоящий код autoISF + виртуальный пациент",
      badge: "UVA/Padova-подобная модель · не клинический инструмент",
      "ctl.subject": "Субъект",
      "ctl.scenario": "Сценарий",
      "ctl.arm.off": "autoISF off",
      "ctl.arm.on": "autoISF on",
      "ctl.compare": "сравнить off/on",
      "ctl.live": "● live",
      "ctl.live.stop": "■ stop",
      "ctl.speed": "скорость",
      "card.tir": "TIR 70–180",
      "card.tbr": "TBR <70",
      "card.lbgi": "LBGI",
      "card.mean": "Средняя BG",
      "card.dose": "Доза",
      "card.smb": "SMB",
      "delta.caption": "разница on − off",
      "delta.tir": "ΔTIR",
      "delta.tbr": "ΔTBR",
      "delta.lbgi": "ΔLBGI",
      "delta.dose": "Δдоза",
      "delta.smb": "ΔSMB",
      "delta.falling": "Δинсулин в падении",
      "legend.cgm": "CGM",
      "legend.plasma": "плазма",
      "legend.smb": "SMB",
      "legend.meal": "еда",
      "legend.band": "70–180",
      "legend.other": "другое плечо",
      "chart.iob": "IOB, ЕД",
      "chart.rate": "U/h →",
      "reason.label": "reason",
      "reason.empty": "—",
      "autoisf.off": "autoISF выключен",
      "autoisf.none": "autoISF: нет данных",
      "autoisf.summary": "autoISF: ISF× средний {mean}, диапазон {min}–{max}, меняется в {pct} % циклов",
      "params.summary": "параметры прогона",
      "params.hint": "(применяются в live-режиме)",
      "params.note": "изменения применятся при запуске ● live",
      "params.copies": "когорта ×",
      "params.seed": "seed",
      "params.noise": "шум",
      "params.scale": "масштаб",
      "params.sigma": "σ, мг/дл",
      "params.ar1": "AR(1) ρ",
      "params.isf": "ISF",
      "params.temp": "темпы",
      "params.smb": "SMB ratio",
      "params.var": "вариативность M3",
      "noise.residuals": "реальные остатки",
      "noise.ar1": "AR(1)",
      "noise.white": "белый",
      "noise.none": "без шума",
      "isf.rule": "правило 1700/TDI",
      "isf.model": "из модели",
      "temp.planned": "30 мин",
      "temp.cycle": "1 цикл",
      "scenario.fcl-24h": "FCL 24 ч",
      "scenario.fcl-exercise": "FCL + нагрузка",
      "scenario.announced-8h": "объявленная еда 8 ч",
      "scenario.fault-delay": "задержка болюса 15 мин",
      "scenario.cgm-bias": "смещение CGM +50",
      "tip.iob": "IOB",
      "tip.rate": "темп",
      "tip.start": "старт, нет истории",
      "live.error": "live error: {msg}",
      "live.interrupted": "live-поток прерван",
      "live.unavailable": "live недоступен: {msg} (нужен python3 python/server/live.py)",
      "live.file": "live-режим требует сервер: python3 python/server/live.py",
      "footer.generated": "Данные предрасчитаны",
      "footer.lang": "Язык",
    },
    en: {
      subtitle: "closed loop: the real autoISF code with a virtual patient",
      badge: "UVA/Padova-like model · not a clinical tool",
      "ctl.subject": "Subject",
      "ctl.scenario": "Scenario",
      "ctl.arm.off": "autoISF off",
      "ctl.arm.on": "autoISF on",
      "ctl.compare": "compare off/on",
      "ctl.live": "● live",
      "ctl.live.stop": "■ stop",
      "ctl.speed": "speed",
      "card.tir": "TIR 70–180",
      "card.tbr": "TBR <70",
      "card.lbgi": "LBGI",
      "card.mean": "Mean BG",
      "card.dose": "Dose",
      "card.smb": "SMB",
      "delta.caption": "on − off difference",
      "delta.tir": "ΔTIR",
      "delta.tbr": "ΔTBR",
      "delta.lbgi": "ΔLBGI",
      "delta.dose": "Δdose",
      "delta.smb": "ΔSMB",
      "delta.falling": "Δinsulin while falling",
      "legend.cgm": "CGM",
      "legend.plasma": "plasma",
      "legend.smb": "SMB",
      "legend.meal": "meal",
      "legend.band": "70–180",
      "legend.other": "other arm",
      "chart.iob": "IOB, U",
      "chart.rate": "U/h →",
      "reason.label": "reason",
      "reason.empty": "—",
      "autoisf.off": "autoISF disabled",
      "autoisf.none": "autoISF: no data",
      "autoisf.summary": "autoISF: ISF× mean {mean}, range {min}–{max}, changes in {pct}% of cycles",
      "params.summary": "run parameters",
      "params.hint": "(applied to live runs)",
      "params.note": "changes apply when you start ● live",
      "params.copies": "cohort ×",
      "params.seed": "seed",
      "params.noise": "noise",
      "params.scale": "scale",
      "params.sigma": "σ, mg/dL",
      "params.ar1": "AR(1) ρ",
      "params.isf": "ISF",
      "params.temp": "temps",
      "params.smb": "SMB ratio",
      "params.var": "M3 variability",
      "noise.residuals": "real residuals",
      "noise.ar1": "AR(1)",
      "noise.white": "white",
      "noise.none": "no noise",
      "isf.rule": "1700/TDI rule",
      "isf.model": "from the model",
      "temp.planned": "30 min",
      "temp.cycle": "1 cycle",
      "scenario.fcl-24h": "FCL 24 h",
      "scenario.fcl-exercise": "FCL + exercise",
      "scenario.announced-8h": "announced meals 8 h",
      "scenario.fault-delay": "bolus delay 15 min",
      "scenario.cgm-bias": "CGM bias +50",
      "tip.iob": "IOB",
      "tip.rate": "rate",
      "tip.start": "start, no history",
      "live.error": "live error: {msg}",
      "live.interrupted": "live stream interrupted",
      "live.unavailable": "live unavailable: {msg} (run python3 python/server/live.py)",
      "live.file": "live mode needs the server: python3 python/server/live.py",
      "footer.generated": "Precomputed data",
      "footer.lang": "Language",
    },
  };

  const params = new URLSearchParams(window.location.search);
  const stored = localStorage.getItem("sim_lang");
  const browser = (navigator.language || "en").slice(0, 2);
  const lang = params.get("lang") || stored || (browser === "ru" ? "ru" : "en");

  window.SIM_LANG = DICT[lang] ? lang : "en";

  window.t = (key, vars) => {
    let text = (DICT[window.SIM_LANG] && DICT[window.SIM_LANG][key]) || DICT.en[key] || key;
    if (vars) {
      for (const [name, value] of Object.entries(vars)) {
        text = text.replace(`{${name}}`, String(value));
      }
    }
    return text;
  };

  window.applyI18n = () => {
    document.documentElement.lang = window.SIM_LANG;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = window.t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
      node.placeholder = window.t(node.dataset.i18nPlaceholder);
    });
    const select = document.getElementById("lang");
    if (select) select.value = window.SIM_LANG;
  };

  window.setLang = (next) => {
    if (!DICT[next]) return;
    window.SIM_LANG = next;
    localStorage.setItem("sim_lang", next);
    window.applyI18n();
    window.dispatchEvent(new Event("sim-lang"));
  };

  document.addEventListener("DOMContentLoaded", () => {
    window.applyI18n();
    const select = document.getElementById("lang");
    if (select) {
      select.addEventListener("change", (event) => window.setLang(event.target.value));
    }
  });
})();
