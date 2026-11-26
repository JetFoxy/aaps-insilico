# aaps-insilico

[English](README.md) | Русский

[![demo](https://img.shields.io/badge/demo-live-brightgreen)](https://jetfoxy.github.io/aaps-insilico/)
[![license: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

Стенд in silico для AAPS + autoISF: настоящий код алгоритма в замкнутом контуре с
UVA/Padova-подобным виртуальным пациентом (уравнения Visentin et al. 2018), лагом и шумом CGM,
физической нагрузкой.

**Не** клинический инструмент и не FDA-принятая популяция. Результат — сравнение вариантов
алгоритма при одинаковых сценариях.

- Статус: W1 принят (паритет моста на записанных данных Nightscout), этапы M0–M8 приняты,
  сравнения A1–A30 приняты. JVM-мост запускает реальные `UnscentedKalmanFilterPlugin`,
  `AutosensDataStoreObject`, `GlucoseStatusCalculatorAutoIsf`, конвейер COB/autosens и
  `DetermineBasalAutoISF`. Отчёты: [`docs/reports/W1.md`](docs/reports/W1.md) и
  [`docs/acceptance/`](docs/acceptance/).
- Сравнения алгоритмов: A1 (5 субъектов, 24 ч), A2 (парная матрица, 30 субъектов × 4 сценария,
  bootstrap CI + Wilcoxon), A3 (CF из модели против правила 1700/TDI), A4 (каталог отказов),
  A5 (autoISF под отказами), A6 (расширенная когорта, шум CGM, переопределение настроек),
  A7 (120 субъектов × 3 сида когорты), A8 (сенсорные модели против реальных трейсов CGM,
  ошибки объявления еды), A9 (кросс-проверка с LoopInsighT1 / UVA-Padova T1DMS), A10 (проверка
  направления с прогоном simglucose level C), A11 (устойчивость к форме шума CGM), A12 (MetaboNet
  как четвёртый источник реального CGM), A13 (первое сравнение off / autoISF / DynISF), A14
  (полная матрица off/autoISF/DynISF на 30 субъектах), A15 (DynISF на когорте 120), A16 (DynISF
  при вариативности M3 и на CF из модели), A17 (DynISF под отказами), A18 (DynISF на 120
  субъектах с вариативностью M3), A19 (DynISF на CF из модели, 120 субъектов), A20 (DynISF под
  отказами, 30 субъектов), A21 (то же с поведенческим слоем / rescue-углеводами), A22
  (поведенческий слой в объявленном сценарии с перекусом), A23 (отказ инфузионной площадки),
  A24 (compression lows), A25 (pre-registered safety gate), A26 (fidelity scorecard против
  реального конверта TimSim), A27 (scorecard с поведенческим слоем), A28 (22-дневный дрейф ISF
  с амплитудой из источника), A29 (scorecard с behaviour и compression), A30 (sweep амплитуды
  дрейфа).
  Сводка на английском: [`docs/acceptance/SUMMARY.en.md`](docs/acceptance/SUMMARY.en.md);
  на русском: [`docs/acceptance/SUMMARY.ru.md`](docs/acceptance/SUMMARY.ru.md).
- What-if на своих данных Nightscout: W1 (импорт), W2 (паритет replay — red, с документированным
  остатком), W3 (модель — только иллюстрация), W4 (веб-UI). Отчёты:
  [`docs/acceptance/W1.md`](docs/acceptance/W1.md)–[`docs/acceptance/W4.md`](docs/acceptance/W4.md).

## Быстрый старт

```bash
git clone <repo> && cd aaps-insilico
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./tools/bootstrap.sh          # AAPS worktree на пине + classpath sim-bridge
.venv/bin/python -m pytest tests/ -q
```

`tools/bootstrap.sh` учитывает `AAPS_REPO`, `AAPS_PIN` и `AAPS_WT`; `SIM_CLASSPATH` и `SIM_JAVA`
переопределяют classpath моста и JVM. Без `data/raw/settings.json` стенд использует
`data/fixtures/prefs.example.json` (нейтральные настройки APS/autoISF), поэтому тесты и
демо-экспорт работают на чистом клоне.

Docker:

```bash
./tools/bootstrap.sh          # один раз: AAPS worktree + classpath sim-bridge
./tools/docker_build.sh       # «сплющивает» classpath в образ
docker compose up -d          # http://127.0.0.1:8766/?live=1
```

Образ самодостаточен во время работы (JRE 21, Python, «сплющенный» classpath моста). Он
собирается из пина AAPS на хосте, а не клонируется, потому что пин-форк приватный; исходники
AAPS в образ не попадают, только скомпилированный classpath (84 МБ).

## Демо-UI

Онлайн-демо: **https://jetfoxy.github.io/aaps-insilico/** (предрасчитанные прогоны; live-режим
требует локального сервера ниже).

`web/` — статическая страница без зависимостей, воспроизводящая предрасчитанные прогоны
замкнутого контура: глюкоза (CGM против плазмы), IOB, temp basal, маркеры SMB, приёмы пищи,
`reason` алгоритма на каждом цикле и сводные метрики. Без сервера и сборки:

```bash
python3 python/runner/export_demo.py     # пишет web/data/runs.js (11 прогонов, ~2 мин)
open web/index.html                      # работает и из file://
```

Прямая ссылка на прогон: `web/index.html?run=adolescent%23001-fcl-exercise-on`; добавь
`&compare=1`, чтобы наложить другое плечо и показать дельты on − off.

Live-режим запускает контур и стримит циклы по SSE (нужен собранный мост):

```bash
python3 python/server/live.py --port 8766
open http://127.0.0.1:8766/?live=1
```

## What-if на своих данных Nightscout

`/whatif.html` импортирует твой трейс Nightscout и прогоняет его через настоящий алгоритм AAPS
в двух режимах:

- **Replay** — CGM фиксируется; алгоритм пересчитывает решения по базалу/SMB с выбранными
  настройками. Показывает, что алгоритм выдал бы, а не что было бы с глюкозой.
- **Model** — субъект когорты, подобранный по TDI/CR/ISF, симулирует глюкозу по реальным
  приёмам пищи и болюсам. Это модель, не прогноз.

Вход: URL Nightscout + read-only токен, 3 дня по умолчанию (до 14), и экспорт настроек AAPS
(JSON + пароль) для весов алгоритма и `ICfg`. Профиль берётся из Nightscout. Варианты:
off / autoISF / DynISF, SMB on/off, веса autoISF и переопределения профиля.

Приватность: токен и пароль живут в памяти только на время одного импорта; кэша на диске нет;
сервер слушает `127.0.0.1`. Реальные трейсы не коммитятся (`data/raw/` в gitignore).

```bash
python3 python/server/live.py --port 8766
open http://127.0.0.1:8766/whatif.html
```

Приёмка: W1 импорт, W2 паритет replay, W3 соответствие модели, W4 UI (`docs/acceptance/W1.md`–`W4.md`).
W2 **red**: покрытие в окне 100 % и вход CGM совпадает, но паритет по факторам (`acceIsf`)
**ограничен пином** — мост работает на upstream AAPS `74271a544b`, а трейс записан более новой
production-версией AAPS с raw-observation estimator и autoISF IOB/COB. Абсолютное воспроизведение
решений AAPS не заявляется.
W3 — иллюстрация: подобранный субъект далёк от пользователя (distance ~0.5), и линия модели
помечена как таковая. Отчёты: `docs/acceptance/W1.md`–`W4.md`.

## Параметры

Размер когорты, шум CGM и настройки алгоритма — общие CLI-опции в `python/runner/options.py`;
их принимают `a2_matrix.py`, `fault_matrix.py`, `export_demo.py` и `server/live.py`:

| Опция | Значение |
|---|---|
| `--cohort-copies N` | добавить N копий базового субъекта с возмущённым всасыванием (30 → 30·(1+N)) |
| `--cohort-seed` | сид детерминированного возмущения |
| `--noise residuals\|ar1\|white\|none\|facchinetti\|dexcom` | модель шума/сенсора CGM |
| `--noise-file PATH` | CSV/NPY остатков вместо фикстуры из репозитория |
| `--noise-scale` | множитель амплитуды (например, 1.5 для более шумного сенсора) |
| `--noise-sigma`, `--noise-ar1` | параметры AR(1)/white, мг/дл и lag-1 корреляция |
| `--pref KEY=VALUE` | переопределить любую настройку AAPS (веса autoISF, лимиты SMB, ...) |
| `--behaviour` | включить поведенческий слой (неточное объявление + rescue-углеводы); используется в `fault_matrix.py` |

`facchinetti` и `dexcom` — опубликованные модели ошибки сенсора (Facchinetti 2014; Vettoretti 2019
Dexcom G6), реализованные по статьям и сверенные с LoopInsighT1 (MIT). Ошибки объявления еды —
поля сценария: `announce_amount_error_frac`, `announce_time_error_min`, `announce_lead_min`
(сценарий `announced-error` в `a2_matrix.py`).

Те же значения можно задать через окружение (CLI важнее, затем env, затем встроенные умолчания):

| Переменная | Значение |
|---|---|
| `SIM_COHORT_COPIES`, `SIM_COHORT_SEED` | расширенная когорта |
| `SIM_NOISE`, `SIM_NOISE_SCALE`, `SIM_NOISE_SIGMA`, `SIM_NOISE_AR1` | шум CGM |
| `SIM_PREFS` | переопределения `key=value;key=value` |
| `SIM_PREFS_FILE` | путь к JSON-объекту с переопределениями |
| `SIM_ISF_SOURCE`, `SIM_TEMP_MODE`, `SIM_VARIABILITY`, `SIM_COMPARE` | режим прогона |
| `SIM_BEHAVIOUR` | включить поведенческий слой (то же, что `--behaviour`) |
| `SIM_HOST`, `SIM_PORT` | адрес live-сервера |

В live-UI те же параметры редактируются на прогон: панель **параметры прогона** над графиками
(размер/сид когорты, модель и амплитуда шума, источник ISF, temp mode, вариативность, SMB ratio,
шесть весов autoISF и min/max). Статическое демо предрасчитано, поэтому панель влияет только на
live-прогоны.

```bash
# 120 пациентов (30 базовых + 3 копии каждого), более шумный CGM, вес ускорения autoISF выключен
.venv/bin/python python/runner/a2_matrix.py --subjects 120 --workers 6 \
    --cohort-copies 3 --noise-scale 1.5 \
    --pref bgAccel_ISF_weight=0.0 --out out/a2_noisy.json
```

Расширенная когорта возмущает только параметры всасывания (`kabs`, `kmax`, `kmin`, `ka1`, `ka2`,
`kd`, `ksc`) и аналитически пересчитывает стационар подкожного инсулина, поэтому каждая копия
стартует с той же глюкозой натощак, что и базовый субъект. `Vmx`, `kp3`, `BW`, `Gb` и `u2ss` не
возмущаются: их популяционный вектор проприетарный, а ре-равновесие не валидировано.

DynISF подключён через адаптер моста (`bridge/.../DynIsfRunner.kt`), который вызывает реальные
`OpenAPSSMBPlugin`/`getIsfMgdl`, а не переписывает их. Используется режим profile-sens
(`dynisf_use_profile_sens`) с константной заглушкой TDD только для прохождения гейта
`tddPartsCalculated()`; калькулятора TDD нет. Сравнения A13–A20 покрывают off / autoISF / DynISF.

## Структура

| Путь | Назначение |
|---|---|
| `python/simcore/` | ядро ОДУ (приложение S2017), решатель, производная когорта, суточная вариативность |
| `python/cgm/` | лаг, калибровка, шум из реальных остатков, каденс, квантование |
| `python/exercise/` | интенсивность → чувствительность, всасывание, хвост восстановления |
| `python/pump/` | квантование доз, лимиты, задержка доставки |
| `python/runner/` | замкнутый контур, метрики, матрицы, демо-экспорт |
| `python/nightscout/` | импорт Nightscout, расшифровка экспорта настроек, нормализация трейса |
| `python/golden/` | W1 replay и проверки паритета на записанных данных Nightscout |
| `bridge/` | JVM-модуль с настоящим кодом AAPS/autoISF (копируется в worktree AAPS) |
| `vendor/deichmann/` | вторая модель для M8, BSD-3, оригинальный код |
| `web/` | статическое демо-UI и вкладка what-if (`whatif.html`) |
| `docs/` | дизайн, реестр параметров, отчёты приёмки по этапам |

## Мост W1

`bridge/` — JVM-модуль Gradle, который копируется в одноразовый worktree пина AAPS. Он запускает
реальные `UnscentedKalmanFilterPlugin`, `AutosensDataStoreObject`,
`GlucoseStatusCalculatorAutoIsf`, `OpenAPSAutoISFPlugin.autoISF()` и
`DetermineBasalAutoISF.determine_basal`.

```
tools/sync_bridge.sh                                  # скопировать bridge/ в $AAPS_WT/sim-bridge
cd "$AAPS_WT" && ./gradlew :sim-bridge:simClasspath   # записать build/sim-classpath.txt
python3 python/golden/check_glucose.py --help
python3 python/golden/check_iob.py --help
python3 python/golden/check_autoisf.py --help
```

В AAPS ничего не коммитится: worktree одноразовый, `sim-bridge/` существует только в нём.

Отчёты в `docs/acceptance/` — на русском (рабочий язык проекта); сводки — на английском
(`SUMMARY.en.md`) и русском (`SUMMARY.ru.md`). Код, README и сообщения коммитов — на английском.

## Лицензия

GPL-3.0-or-later. JVM-мост в `bridge/` компилируется против классов AndroidAPS, а AndroidAPS
распространяется под GPL-3.0, поэтому совместная работа — под той же лицензией. Python-часть не
линкуется с AAPS и при необходимости может быть лицензирована отдельно.

`vendor/deichmann/` — оригинальный код Deichmann et al. 2023 под BSD-3-Clause (см. его
`LICENSE` и `PROVENANCE.md`); BSD-3 совместим с GPL-3.0. Данные пациентов из того репозитория не
вендорятся. Сам AAPS не вендорится — мост копируется в одноразовый worktree во время сборки.
