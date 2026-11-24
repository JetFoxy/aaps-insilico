# What-if на данных Nightscout — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать возможность прогнать свой трейс из Nightscout через настоящий Kotlin-код AAPS и увидеть, как меняются решения (replay) и глюкоза (model) при других коэффициентах autoISF и другом алгоритме.

**Architecture:** Новый пакет `python/nightscout` импортирует и нормализует данные; `python/runner/replay_loop.py` гоняет реальный CGM через мост, не пересчитывая глюкозу; `python/runner/whatif_model.py` подбирает субъекта когорты и считает модель; `python/server/live.py` получает `/api/whatif/*` и SSE; `web/whatif.*` — вкладка.

**Tech Stack:** Python 3.14 (`numpy`, `scipy`, `cryptography`), JVM-мост `sim-bridge`, stdlib HTTP/SSE, ванильный JS.

**Spec:** `docs/superpowers/specs/2026-11-23-whatif-nightscout-design.md`

## Global Constraints

- Все времена — epoch ms; никаких наивных datetime в данных.
- Сенсорный вход — сырые отметки Nightscout; решения — на 5-минутной сетке.
- Токен Nightscout и пароль экспорта не попадают в stdout, stderr, файлы, git, тесты и отчёты.
- В репозиторий не коммитятся личные трейсы; фикстуры только синтетические.
- Код без комментариев; исключения — докстринги, как в соседних файлах.
- `requirements.txt` получает `cryptography`; версия фиксируется.
- В рабочем дереве уже есть несвязанные изменения (`bridge/`, `python/`): каждый коммит
  добавляет только файлы, перечисленные в задаче.
- Допуски W2 фиксируются в Task 7 до первого прогона и не меняются после.
- В replay TIR/TBR не считаются; model всегда подписан как модель.

## Review Focus

1. Токен или URL Nightscout утекает в текст ошибки, stdout или сохранённый JSON — тест в Task 1 и Task 4.
2. SMB-болюсы остаются во входе и складываются с SMB варианта — тест в Task 3 и Task 6.
3. Короткий temp basal (2–9 мин) трактуется как 30-минутный — тест в Task 3 и Task 6.
4. Temp target применяется вне своего окна или игнорируется — тест в Task 3 и Task 6.
5. В replay показывается TBR, а model выдаётся за реальный день — тест в Task 9 и чек-лист Task 10.

---

### Task 1: Клиент Nightscout с редактированием секретов

**Files:**
- Create: `python/nightscout/__init__.py`
- Create: `python/nightscout/client.py`
- Create: `tests/nightscout_stub.py`
- Test: `tests/test_nightscout_client.py`

**Interfaces:**
- Produces:
  - `class NightscoutError(RuntimeError)` с атрибутом `status: int | None`.
  - `class NightscoutClient(base_url: str, token: str | None = None, api_secret: str | None = None, timeout: float = 30.0)`
  - `.status() -> dict`, `.profile() -> list[dict]`
  - `.entries(start_ms: int, end_ms: int, count: int = 20000) -> list[dict]`
  - `.treatments(start_ms: int, end_ms: int, count: int = 5000) -> list[dict]`
  - `.devicestatus(start_ms: int, end_ms: int, count: int = 5000) -> list[dict]`
  - `tests/nightscout_stub.py`: `class StubNightscout` с `url: str`, `requests: list[tuple[str, dict]]`,
    конструктор `StubNightscout(routes: dict[str, Callable[[dict], tuple[int, object]]])`.

- [ ] **Step 1: Написать падающий тест**

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nightscout.client import NightscoutClient, NightscoutError
from nightscout_stub import StubNightscout


def test_entries_use_epoch_filter_and_token():
    stub = StubNightscout({"/api/v1/entries.json": lambda q: (200, [{"sgv": 100}])})
    with stub:
        client = NightscoutClient(stub.url, token="tok-123")
        assert client.entries(1_700_000_000_000, 1_700_086_400_000) == [{"sgv": 100}]
    path, query = stub.requests[0]
    assert path == "/api/v1/entries.json"
    assert query["find[date][$gte]"] == "1700000000000"
    assert query["find[type]"] == "sgv"
    assert query["token"] == "tok-123"


def test_token_never_appears_in_error():
    stub = StubNightscout({"/api/v1/entries.json": lambda q: (401, {"status": 401})})
    with stub:
        client = NightscoutClient(stub.url, token="tok-secret")
        with pytest.raises(NightscoutError) as exc:
            client.entries(0, 1)
    assert "tok-secret" not in str(exc.value)
    assert exc.value.status == 401


def test_api_secret_fallback_on_401():
    calls = []

    def route(query):
        calls.append(dict(query))
        return (401, {}) if "token" in query else (200, [{"sgv": 101}])

    stub = StubNightscout({"/api/v1/entries.json": route})
    with stub:
        client = NightscoutClient(stub.url, token="tok", api_secret="abc")
        assert client.entries(0, 1) == [{"sgv": 101}]
    assert "api-secret" in stub.requests[-1][1]
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_nightscout_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nightscout'`

- [ ] **Step 3: Реализовать клиент и заглушку**

`NightscoutClient` хранит `base_url.rstrip("/")`, токен и секрет; `_get(path, params)` собирает
`urllib.parse.urlencode(params)`, добавляет `token`, при 401 повторяет с заголовком
`api-secret` (sha1-хэш не нужен: AAPS принимает сам секрет как заголовок, если инстанс так настроен;
если нет — второй 401). Все ошибки формируются через `_redact(text)`, который заменяет
`token=...` на `token=***` и `api-secret` на `***`.

`StubNightscout` поднимает `ThreadingHTTPServer(("127.0.0.1", 0), Handler)` в демон-потоке,
`url` — `http://127.0.0.1:<port>`, `__enter__`/`__exit__` запускают и гасят сервер.
Query разбирается `urllib.parse.parse_qs` в плоский dict первых значений.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_nightscout_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Коммит**

```bash
git add python/nightscout/__init__.py python/nightscout/client.py tests/nightscout_stub.py tests/test_nightscout_client.py
git commit -m "feat(nightscout): read-only client with secret redaction"
```

---

### Task 2: Расшифровка экспорта настроек AAPS

**Files:**
- Create: `python/nightscout/prefs.py`
- Modify: `requirements.txt`
- Test: `tests/test_nightscout_prefs.py`

**Interfaces:**
- Produces:
  - `class PrefsDecryptError(ValueError)`
  - `decode_export(text: str, password: str | None) -> dict[str, object]`
- Consumes: ничего из предыдущих задач.

- [ ] **Step 1: Добавить зависимость и написать падающий тест**

`requirements.txt` — добавить `cryptography`, затем зафиксировать точную версию, которую поставил
pip (`pip show cryptography`), чтобы сборка была воспроизводимой.

```python
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.prefs import PrefsDecryptError, decode_export


def encode(prefs: dict, password: str) -> str:
    raw = json.dumps(prefs, sort_keys=True)
    salt = os.urandom(32)
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=32, salt=salt, iterations=50000).derive(password.encode())
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, raw.encode(), None)
    content = base64.b64encode(bytes([len(iv)]) + iv + sealed).decode()
    return json.dumps({
        "metadata": {}, "format": "aaps_encrypted",
        "security": {"salt": salt.hex(), "algorithm": "v1",
                     "content_hash": hashlib.sha256(raw.encode()).hexdigest()},
        "content": content,
    })


def test_round_trip():
    text = encode({"a": "1", "insulin_configuration": "{}"}, "pw")
    assert decode_export(text, "pw") == {"a": "1", "insulin_configuration": "{}"}


def test_wrong_password_does_not_echo_it():
    text = encode({"a": "1"}, "pw")
    with pytest.raises(PrefsDecryptError) as exc:
        decode_export(text, "nope-secret")
    assert "nope-secret" not in str(exc.value)


def test_missing_password_is_reported():
    text = encode({"a": "1"}, "pw")
    with pytest.raises(PrefsDecryptError, match="password"):
        decode_export(text, None)


def test_plain_prefs_are_returned_as_is():
    assert decode_export('{"a": "1"}', None) == {"a": "1"}
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_nightscout_prefs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nightscout.prefs'`

- [ ] **Step 3: Реализовать `decode_export`**

Разобрать JSON. Если нет `format`/`security`/`content` — вернуть объект как есть (это уже плоский
prefs). Если `format == "aaps_encrypted"`: без пароля — `PrefsDecryptError("password required for
this export")`; `salt = bytes.fromhex(security["salt"])`; `envelope = base64.b64decode(content)`;
`iv_len = envelope[0]`; `iv = envelope[1:1+iv_len]`; `sealed = envelope[1+iv_len:]`;
`key = PBKDF2HMAC(SHA1, 32, salt, 50000)`; `AESGCM(key).decrypt(iv, sealed, None)`.
Проверить `sha256(plaintext).hexdigest() == security.get("content_hash")`; при несовпадении —
`PrefsDecryptError("export content hash mismatch")`. `InvalidTag` перехватить и превратить в
`PrefsDecryptError("cannot decrypt export: wrong password or damaged file")` — без текста исключения.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_nightscout_prefs.py -v`
Expected: 4 passed

- [ ] **Step 5: Коммит**

```bash
git add python/nightscout/prefs.py tests/test_nightscout_prefs.py requirements.txt
git commit -m "feat(nightscout): decode encrypted AAPS settings export"
```

---

### Task 3: Нормализация трейса и профиля

**Files:**
- Create: `python/nightscout/trace.py`
- Test: `tests/test_nightscout_trace.py`

**Interfaces:**
- Produces:
  - `CgmPoint(ts: int, mgdl: float, direction: str = "NONE", device: str = "")`
  - `Treatment(ts: int, kind: str, amount: float = 0.0, duration_min: float = 0.0, origin: str = "manual", meta: dict = field(default_factory=dict))`
  - `DecisionRecord(ts: int, deliver_at: int, insulin_req: float | None, final_isf: float | None, factors: dict[str, float], smb_on: bool, rate: float | None, eventual_bg: float | None, sensitivity_ratio: float | None)`
  - `ProfileSegment(start_ms: int, basal: list[tuple[int, float]], sens: list[tuple[int, float]], carb_ratio: list[tuple[int, float]], target_low: list[tuple[int, float]], target_high: list[tuple[int, float]], units: str, timezone: str)`
  - `DayTrace(entries, treatments, profiles, decisions, window_start, window_end, timezone, sensor, warnings)`
    с методами `profile_at(ts: int) -> ProfileSegment`, `active_temp_target(ts: int) -> tuple[float, float] | None`,
    `autosens_at(ts: int) -> float`, `temp_basal_at(ts: int) -> tuple[float, float] | None`.
  - `ProfileSegment.basal_at(ts: int) -> float`, `.sens_at(ts) -> float`, `.carb_ratio_at(ts) -> float`,
    `.target_low_at(ts) -> float`, `.target_high_at(ts) -> float` — значение последней записи с
    `time <= минута_суток`, иначе первая запись.
  - `build_trace(*, entries: list[dict], treatments: list[dict], profiles: list[dict], devicestatus: list[dict], window_start: int, window_end: int, timezone: str | None = None, history_start: int | None = None) -> DayTrace`
    — `history_start` по умолчанию `window_start`; точки CGM с `history_start <= ts <= window_end`
    сохраняются (нужны для окна сенсора первого решения), решения/лечение не обрезаются.

- [ ] **Step 1: Написать падающий тест**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.trace import build_trace

T0 = 1_790_000_000_000
MIN = 60_000


def profile_doc(start, sens, cr, basal, low=5.5, high=7.0):
    return {
        "startDate": start, "defaultProfile": "Main",
        "store": {"Main": {
            "units": "mmol", "timezone": "Europe/Moscow",
            "sens": [{"time": "00:00", "value": sens}],
            "carbratio": [{"time": "00:00", "value": cr}],
            "basal": [{"time": "00:00", "value": basal}],
            "target_low": [{"time": "00:00", "value": low}],
            "target_high": [{"time": "00:00", "value": high}],
        }},
    }


def test_build_trace_normalizes_units_and_events():
    entries = [
        {"type": "sgv", "date": T0 + 1 * MIN, "sgv": 120, "direction": "Flat"},
        {"type": "sgv", "date": T0 + 2 * MIN, "sgv": 121, "direction": "Flat"},
        {"type": "cal", "date": T0 + 3 * MIN, "sgv": 0},
    ]
    treatments = [
        {"eventType": "Correction Bolus", "created_at": "2026-09-01T00:00:00Z",
         "date": T0, "insulin": 0.6, "type": "SMB"},
        {"eventType": "Correction Bolus", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "insulin": 2.0, "type": "NORMAL"},
        {"eventType": "Temp Basal", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "absolute": 1.2, "duration": 6},
        {"eventType": "Temporary Target", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "targetBottom": 5.0, "targetTop": 6.0, "duration": 20},
    ]
    devicestatus = [{"created_at": "2026-09-01T00:05:00Z", "openaps": {"suggested": {
        "deliverAt": "2026-09-01T00:05:00Z", "insulinReq": 0.4,
        "sensitivityRatio": 0.95, "rate": 1.1,
        "autoIsf": {"acceIsf": 1.1, "bgIsf": 0.9, "ppIsf": 1.0, "duraIsf": 1.0,
                    "finalIsf": 0.99, "smbOn": True},
    }}}]
    trace = build_trace(entries=entries, treatments=treatments,
                        profiles=[profile_doc(T0 - 86_400_000, 50.0, 10.0, 1.0)],
                        devicestatus=devicestatus, window_start=T0, window_end=T0 + 3600_000)
    assert [p.mgdl for p in trace.entries] == [120, 121]
    assert trace.profile_at(T0).sens[0][1] == 900.0
    assert trace.profile_at(T0).carb_ratio[0][1] == 180.0
    assert trace.treatments[0].origin == "loop"
    assert trace.treatments[1].origin == "manual"
    assert trace.active_temp_target(T0 + 10 * MIN) == (5.0 * 18, 6.0 * 18)
    assert trace.active_temp_target(T0 + 30 * MIN) is None
    assert trace.autosens_at(T0 + 10 * MIN) == 0.95
    assert trace.temp_basal_at(T0 + 7 * MIN) == (1.2, 4.0)
    assert trace.decisions[0].factors["acceIsf"] == 1.1
    assert trace.decisions[0].smb_on is True


def test_profile_values_follow_time_of_day():
    doc = profile_doc(T0 - 86_400_000, 50.0, 10.0, 1.0)
    doc["store"]["Main"]["basal"] = [{"time": "00:00", "value": 0.8}, {"time": "06:00", "value": 1.4}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}], treatments=[],
                        profiles=[doc], devicestatus=[], window_start=T0, window_end=T0 + 1)
    segment = trace.profile_at(T0)
    assert segment.basal_at(T0) in (0.8, 1.4)
    assert segment.sens_at(T0) == 900.0


def test_untyped_bolus_is_reported_not_guessed():
    treatments = [{"eventType": "Correction Bolus", "date": T0, "insulin": 1.0}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments, profiles=[profile_doc(T0, 50.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 1)
    assert trace.treatments[0].origin == "unknown"
    assert any("unclassified" in w for w in trace.warnings)
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_nightscout_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nightscout.trace'`

- [ ] **Step 3: Реализовать `build_trace`**

Правила:

- `entries`: только `type == "sgv"`, `window_start <= date <= window_end`; дубли по `date` —
  последний; сортировка по возрастанию.
- `treatments`: `eventType` → `kind` (`Correction Bolus`/`Meal Bolus`/`Bolus` → `bolus`,
  `Carb Correction`/`Meal Bolus` с `carbs` → дополнительно `carbs`, `Temp Basal` → `temp_basal`,
  `Temporary Target` → `temp_target`, `Profile Switch` → `profile_switch`, иначе `note`).
  Bolus: `origin = "loop"` при `type == "SMB"`, `"manual"` при `type == "NORMAL"`, иначе `"unknown"`
  (и warning `unclassified bolus: <n>`). Temp basal: `amount = absolute` или `rate`,
  `duration_min = duration`. TT: `meta = {"low": targetBottom*18, "high": targetTop*18}`.
- `profiles`: `startDate` → ms; поля `basal`/`sens`/`carbratio`/`target_low`/`target_high` —
  списки `{"time": "HH:MM", "value": ...}`; `sens`, `carbratio`, цели умножить на 18 при
  `units == "mmol"`; `basal` не умножать. Отсортировать сегменты по `start_ms`.
- `decisions`: `openaps.suggested`; `deliver_at` — из `deliverAt` (ISO или ms), `ts` — из
  `created_at`; дубли по `deliver_at` — последний; `final_isf` из `autoIsf.finalIsf`, `factors` —
  остальные ключи `autoIsf`, `smb_on` — bool, `rate`, `insulinReq`, `eventualBG`,
  `sensitivityRatio`.
- `warnings`: `"no devicestatus: parity check disabled"` при пустом списке; `"no profile"` при
  пустом profiles; `"unclassified bolus: N"`; `"cgm gaps > 15 min: N"`.
- `profile_at`: последний сегмент с `start_ms <= ts`, иначе первый.
- `active_temp_target`: события `temp_target` с `ts <= now < ts + duration_min*60_000`; вернуть
  последний по времени.
- `autosens_at`: ближайшее `decision` с `ts <= now`; иначе 1.0.
- `temp_basal_at`: последний `temp_basal` с `ts <= now`; вернуть `(amount, remaining_min)`,
  где `remaining = duration_min - (now - ts)/60_000`; если `remaining <= 0` — `None`.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_nightscout_trace.py -v`
Expected: 2 passed

- [ ] **Step 5: Коммит**

```bash
git add python/nightscout/trace.py tests/test_nightscout_trace.py
git commit -m "feat(nightscout): normalize trace, profile segments and decision records"
```

---

### Task 4: CLI импорта и отчёт W1

**Files:**
- Create: `python/nightscout/cli.py`
- Create: `python/nightscout/__main__.py`
- Create: `docs/acceptance/W1.md`
- Test: `tests/test_nightscout_cli.py`

**Interfaces:**
- Consumes: `NightscoutClient`, `decode_export`, `build_trace`.
- Produces: `main(argv: list[str] | None = None) -> int`; подкоманда `import`.

- [ ] **Step 1: Написать падающий тест**

```python
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nightscout.cli import main
from nightscout_stub import StubNightscout

NOW = int(time.time() * 1000)
PROFILE = {"startDate": NOW - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
    "units": "mmol", "timezone": "Europe/Moscow",
    "sens": [{"time": "00:00", "value": 5.0}],
    "carbratio": [{"time": "00:00", "value": 10.0}],
    "basal": [{"time": "00:00", "value": 1.0}],
    "target_low": [{"time": "00:00", "value": 5.5}],
    "target_high": [{"time": "00:00", "value": 7.0}],
}}}


def test_import_summary_has_no_token():
    routes = {
        "/api/v1/status.json": lambda q: (200, {"apiEnabled": True}),
        "/api/v1/profile.json": lambda q: (200, [PROFILE]),
        "/api/v1/entries.json": lambda q: (200, [{"type": "sgv", "date": NOW - 60_000, "sgv": 110}]),
        "/api/v1/treatments.json": lambda q: (200, []),
        "/api/v1/devicestatus.json": lambda q: (200, []),
    }
    stub = StubNightscout(routes)
    out = io.StringIO()
    with stub, redirect_stdout(out):
        code = main(["import", "--url", stub.url, "--token", "tok-secret", "--days", "1"])
    text = out.getvalue()
    assert code == 0
    assert "tok-secret" not in text
    summary = json.loads(text)
    assert summary["entries"] == 1
    assert summary["prefs_loaded"] is False
    assert any("devicestatus" in w for w in summary["warnings"])
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_nightscout_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nightscout.cli'`

- [ ] **Step 3: Реализовать CLI**

`main` разбирает `import --url --token --days --prefs --password --out`. Считает
`window_end = now_ms`, `window_start = window_end - days*86_400_000`, `history_start =
window_start - 8*3_600_000` (lookback для seed history replay и окна сенсора). Загружает status,
profile, entries, treatments, devicestatus **с `history_start`**, а в `build_trace` передаёт
`window_start`/`window_end` и `history_start`. Если `--prefs` задан — читает файл и вызывает
`decode_export(text, password)`, ставит `prefs_loaded=True`. Строит trace и печатает **только**
сводку:

```json
{"entries": 4133, "treatments": 1106, "decisions": 711, "profiles": 15,
 "window_start": 0, "window_end": 0, "sensor": "Libre2", "prefs_loaded": true,
 "warnings": []}
```

`--out` пишет полный трейс в JSON (явный запрос пользователя, не кэш). В `--out` не должно быть
`token`, `url`, `password`.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_nightscout_cli.py -v`
Expected: 1 passed

- [ ] **Step 5: Написать `docs/acceptance/W1.md`**

Разделы: постановка, что импортируется, синтетический прогон (команда + вывод без личных данных),
проверка «токен не в логе», ограничения (нет exercise, нет кэша), статус «принят».

- [ ] **Step 6: Коммит**

```bash
git add python/nightscout/cli.py python/nightscout/__main__.py tests/test_nightscout_cli.py docs/acceptance/W1.md
git commit -m "feat(nightscout): import CLI and W1 acceptance report"
```

---

### Task 5: Мост отдаёт факторы autoISF

**Files:**
- Modify: `bridge/src/main/kotlin/app/aaps/sim/AutoIsf.kt`
- Modify: `bridge/src/main/kotlin/app/aaps/sim/SimBridge.kt:375-411`
- Test: `tests/test_bridge_aiv.py` (помечен `pytest.mark.bridge`, по умолчанию skip)

**Interfaces:**
- Produces: ответ `autoisf` получает поле `aiv`:
  `{"acceIsf": float, "bgIsf": float, "ppIsf": float, "duraIsf": float, "finalIsf": float, "iobThEffective": float}`.

- [ ] **Step 1: Написать падающий тест**

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

CLASSPATH = Path(os.environ.get(
    "SIM_CLASSPATH",
    Path.home() / "Projects/Dia/AndroidAPS-w1/sim-bridge/build/sim-classpath.txt",
))
pytestmark = [
    pytest.mark.bridge,
    pytest.mark.skipif(not CLASSPATH.exists(), reason="sim-bridge classpath missing"),
]


def test_autoisf_returns_factors():
    from bridge.client import Bridge, insulin_from_settings, load_prefs
    root = Path(__file__).resolve().parents[1]
    prefs = load_prefs(root / "data/raw/settings.json")
    insulin = insulin_from_settings(root / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    with Bridge() as bridge:
        bridge.request({"cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
                        "treatments": [], "basal": [[1_790_000_000_000, 1.0]], "timezone": "Europe/Moscow"})
        response = bridge.request({
            "cmd": "autoisf", "now": 1_790_000_300_000,
            "readings": [[1_790_000_300_000 - i * 60_000, 120.0] for i in range(30)],
            "isf": 50.0, "carbRatio": 10.0, "target": 100.0, "currentBasal": 1.0,
        })
    aiv = response["aiv"]
    assert set(aiv) == {"acceIsf", "bgIsf", "ppIsf", "duraIsf", "finalIsf", "iobThEffective"}
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_bridge_aiv.py -v -m bridge`
Expected: FAIL — `KeyError: 'aiv'`

- [ ] **Step 3: Добавить `aiv()` и поле в ответ**

В `AutoIsfRunner` добавить метод по образцу `console()`. Поле в `OpenAPSAutoISFPlugin` называется
`autoIsfValues`, класс — `app.aaps.core.data.model.AIV` (проверено по harness
`AutoIsfPipelineSimulationTest.aivField()`):

```kotlin
fun aiv(): Map<String, Double> {
    val field = OpenAPSAutoISFPlugin::class.java.getDeclaredField("autoIsfValues")
    field.isAccessible = true
    val aiv = field.get(plugin) as AIV
    return mapOf(
        "acceIsf" to aiv.acceIsf, "bgIsf" to aiv.bgIsf, "ppIsf" to aiv.ppIsf,
        "duraIsf" to aiv.duraIsf, "finalIsf" to aiv.finalIsf, "iobThEffective" to aiv.iobThEffective,
    )
}
```

Импорт `app.aaps.core.data.model.AIV`. В `SimBridge.autoisf` добавить
`put("aiv", buildJsonObject { autoIsf.aiv().forEach { (k, v) -> put(k, JsonPrimitive(v)) } })`.

Дополнительно (нужно replay): `SimBridge.meal` сейчас читает только `carbs`, загруженные в `init`,
и игнорирует углеводы в `treatments`. Сделать как в `iob`: если `treatments` не `null`, собрать
`CA` из элементов с `kind == "carbs"` (`ts`, `duration`, `amount`) и передать их в `CobRunner`;
иначе — глобальный `carbs`. `lastCarbTime` тоже считать по выбранному списку. Добавить bridge-тест:
`cycle` с одним `carbs`-лечением даёт `meal.lastCarbTime == ts` и `meal.mealCOB > 0`.

- [ ] **Step 4: Пересобрать мост и прогнать тест**

```bash
./tools/sync_bridge.sh
cd ~/Projects/Dia/AndroidAPS-w1 && ./gradlew :sim-bridge:simClasspath --configure-on-demand -q
cd ~/Projects/Dia/aaps-insilico && pytest tests/test_bridge_aiv.py -v -m bridge
```

Expected: 1 passed

- [ ] **Step 5: Коммит**

```bash
git add bridge/src/main/kotlin/app/aaps/sim/AutoIsf.kt bridge/src/main/kotlin/app/aaps/sim/SimBridge.kt tests/test_bridge_aiv.py pytest.ini
git commit -m "feat(bridge): expose autoISF factors in the autoisf response"
```

---

### Task 6: Replay-движок

**Files:**
- Create: `python/runner/replay_loop.py`
- Test: `tests/test_replay_loop.py`

**Interfaces:**
- Consumes: `DayTrace` из Task 3; мост с командами `init`/`cycle`.
- Produces:
  - `ReplayVariant(label: str, algorithm: str = "autoisf", smb: bool = True, prefs: dict = field(default_factory=dict), profile_overrides: dict = field(default_factory=dict))`
  - `ReplayCycle(ts: int, glucose: float, rate: float | None, smb: float, insulin_req: float | None, final_isf: float | None, factors: dict[str, float], iob: float | None, eventual_bg: float | None, sensitivity_ratio: float | None, reason: str)`
  - `ReplayResult(variant, cycles: list[ReplayCycle], skipped: list[int], warnings: list[str])`
  - `run_replay(trace, prefs, insulin, variant, bridge, *, cadence_min=5.0, readings_window_min=180, seed_history_hours=8.0) -> ReplayResult`
  - `replay_to_dict(result) -> dict`, `replay_from_dict(data) -> ReplayResult`

- [ ] **Step 1: Написать падающий тест**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.trace import build_trace
from runner.replay_loop import ReplayVariant, run_replay

T0 = 1_790_000_000_000
MIN = 60_000


class FakeBridge:
    def __init__(self):
        self.requests = []
        self.n = 0

    def request(self, payload):
        self.requests.append(payload)
        if payload["cmd"] == "init":
            return {"ok": True}
        self.n += 1
        return {
            "rt": {"rate": 1.1, "units": 0.2, "insulinReq": 0.4, "eventualBG": 130.0,
                   "sensitivityRatio": 0.95, "reason": "test"},
            "algorithm": {"sens": 52.0, "aiv": {
                "acceIsf": 1.1, "bgIsf": 0.9, "ppIsf": 1.0, "duraIsf": 1.0,
                "finalIsf": 52.0, "iobThEffective": 5.0}},
            "iob": {"iob": [{"iob": 1.5}]},
        }


def make_trace():
    entries = [{"type": "sgv", "date": T0 + i * MIN, "sgv": 120 + i} for i in range(0, 40, 2)]
    treatments = [{"eventType": "Temporary Target", "date": T0, "targetBottom": 5.0,
                   "targetTop": 6.0, "duration": 20}]
    profiles = [{"startDate": T0 - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
        "units": "mmol", "timezone": "Europe/Moscow",
        "sens": [{"time": "00:00", "value": 5.0}],
        "carbratio": [{"time": "00:00", "value": 10.0}],
        "basal": [{"time": "00:00", "value": 1.0}],
        "target_low": [{"time": "00:00", "value": 5.5}],
        "target_high": [{"time": "00:00", "value": 7.0}],
    }}}]
    return build_trace(entries=entries, treatments=treatments, profiles=profiles,
                       devicestatus=[], window_start=T0, window_end=T0 + 30 * MIN)


def test_replay_passes_targets_autosens_and_keeps_own_smb():
    trace = make_trace()
    bridge = FakeBridge()
    variant = ReplayVariant(label="A", prefs={"openapsama_smb_delivery_ratio": "0.7"})
    result = run_replay(trace, {"openapsama_smb_delivery_ratio": "0.3"}, {"label": "x"}, variant, bridge)
    init = bridge.requests[0]
    assert init["cmd"] == "init"
    cycles = [r for r in bridge.requests if r["cmd"] == "cycle"]
    assert len(cycles) == 7
    assert cycles[0]["input"]["profile"]["temptargetSet"] is True
    assert cycles[0]["input"]["autosens"]["ratio"] == 1.0
    assert cycles[0]["input"]["smbRatio"] == 0.7
    assert cycles[0]["input"]["microBolusAllowed"] is True
    assert result.cycles[0].smb == 0.2
    assert any(t["kind"] == "bolus" and t["amount"] == 0.2 and t["type"] == "SMB"
               for t in cycles[1]["treatments"])


def test_init_basal_follows_the_profile_schedule():
    trace = make_trace()
    trace.profiles[0] = type(trace.profiles[0])(
        start_ms=trace.profiles[0].start_ms,
        basal=[(0, 0.8), (360, 1.4)],
        sens=trace.profiles[0].sens,
        carb_ratio=trace.profiles[0].carb_ratio,
        target_low=trace.profiles[0].target_low,
        target_high=trace.profiles[0].target_high,
        units=trace.profiles[0].units,
        timezone=trace.profiles[0].timezone,
    )
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    blocks = bridge.requests[0]["basal"]
    assert len(blocks) >= 2
    assert any(rate == 1.4 for _, rate in blocks)


def test_seed_history_excludes_smb_and_keeps_short_temp_duration():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 60 * MIN, kind="bolus", amount=0.9, duration_min=0.0, origin="loop"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 30 * MIN, kind="temp_basal", amount=1.4, duration_min=6.0, origin="pump"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    seeded = bridge.requests[0]["treatments"]
    assert all(not (t["kind"] == "bolus" and t["amount"] == 0.9) for t in seeded)
    temp = next(t for t in seeded if t["kind"] == "temp")
    assert temp["rate"] == 1.4
    assert temp["duration"] == 6 * 60_000
    first_cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert any(t["kind"] == "temp" and t["rate"] == 1.4 for t in first_cycle["treatments"])


def test_replay_skips_cgm_gap_and_reports_it():
    trace = make_trace()
    trace.entries = [e for e in trace.entries if e.ts < T0 + 10 * MIN]
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    assert result.skipped
    assert any("gap" in w for w in result.warnings)


def test_in_window_real_boluses_and_carbs_reach_the_bridge():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="bolus", amount=3.0, duration_min=0.0, origin="manual"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="carbs", amount=40.0, duration_min=0.0, origin="manual"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="bolus", amount=0.7, duration_min=0.0, origin="loop"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    early = [r for r in bridge.requests if r["cmd"] == "cycle"][0]
    later = [r for r in bridge.requests if r["cmd"] == "cycle"][3]
    assert all(not (t["kind"] == "bolus" and t["amount"] == 3.0) for t in early["treatments"])
    assert any(t["kind"] == "bolus" and t["amount"] == 3.0 for t in later["treatments"])
    assert any(t["kind"] == "carbs" and t["amount"] == 40.0 for t in later["treatments"])
    assert all(not (t["kind"] == "bolus" and t["amount"] == 0.7) for t in later["treatments"])
    assert later["mealMode"] == "announced"


def test_in_window_untyped_bolus_is_included_with_warning():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 5 * MIN, kind="bolus", amount=1.5, duration_min=0.0, origin="unknown"))
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    last = [r for r in bridge.requests if r["cmd"] == "cycle"][-1]
    assert any(t["kind"] == "bolus" and t["amount"] == 1.5 for t in last["treatments"])
    assert any("unclassified" in w for w in result.warnings)


def test_replay_uses_recorded_decision_times():
    trace = make_trace()
    from nightscout.trace import DecisionRecord
    trace.decisions = [
        DecisionRecord(ts=T0 + 2 * MIN, deliver_at=T0 + 2 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
        DecisionRecord(ts=T0 + 5 * MIN, deliver_at=T0 + 5 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
        DecisionRecord(ts=T0 + 9 * MIN, deliver_at=T0 + 9 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
    ]
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    times = [r["now"] for r in bridge.requests if r["cmd"] == "cycle"]
    assert times == [T0 + 2 * MIN, T0 + 5 * MIN, T0 + 9 * MIN]


def test_smb_off_disables_micro_bolus():
    trace = make_trace()
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A", smb=False), bridge)
    cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert cycle["input"]["microBolusAllowed"] is False
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_replay_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.replay_loop'`

- [ ] **Step 3: Реализовать `run_replay`**

Алгоритм:

1. `prefs_effective = {**prefs, **variant.prefs}`; `algorithm = variant.algorithm`.
2. `init`: `prefs`, `profiles=[]`, `insulin`, `treatments` — история за `seed_history_hours` до
   `window_start` в формате моста:
   - болюс: `{"kind":"bolus","ts":ms,"amount":U,"type":"NORMAL"|"SMB","isValid":True}`;
   - углеводы: `{"kind":"carbs","ts":ms,"amount":g,"duration":0}`;
   - temp basal: `{"kind":"temp","ts":ms,"duration":ms,"rate":U/h,"isAbsolute":True,"isValid":True}`.
   SMB-болюсы (`origin == "loop"`) в историю не попадают. `basal` — не один блок, а полный
   распорядок профиля на окно: для каждого сегмента профиля и каждого дня окна выдать
   `[ts, rate]` для всех точек `ProfileSegment.basal` (в таймзоне сегмента), плюс
   `[window_start, basal_at(window_start)]` как первый блок; `timezone`.
   **Журнал варианта инициализируется этой же историей**: мост при непустом `treatments`
   полностью заменяет ими свою историю, поэтому пустой журнал на первом цикле обнулил бы IOB
   от реальных болюсов.
3. Точки решения: отсортированные уникальные `deliver_at` из `trace.decisions` в окне
   (реальный цикл AAPS в проверенных данных идёт каждые ~2–3 мин, не по 5-минутной сетке);
   если решений в трейсе нет — 5-минутная сетка от `window_start`. `cadence_min` остаётся для
   длительности temp basal и подсчёта пропусков.
4. Для каждой: `readings = [[ts, mgdl] for entries in [now - readings_window_min, now]]`
   по убыванию; если последняя точка старше `now - 15*60_000` — в `skipped`, продолжить.
5. `temp_target = trace.active_temp_target(now)` → `{"ts": now, "reason": "Custom", "high": high,
   "low": low, "duration": remaining}`; `temptargetSet = temp_target is not None`.
6. `current_temp`: из журнала варианта последний temp basal с `remaining > 0` →
   `{"duration": int(ceil(remaining)), "rate": rate, "minutesrunning": int(elapsed)}`;
   иначе `{"duration": 0, "rate": 0.0, "minutesrunning": 0}` — как в `closed_loop.py:220-228`.
7. `cycle` с полями из `closed_loop.py:236-271` (см. существующий вызов), но
   профиль — `trace.profile_at(now)` с наложенными `variant.profile_overrides`,
   `smbRatio = float(prefs_effective.get("openapsama_smb_delivery_ratio", 0.5))`,
   `autosens = trace.autosens_at(now)`, `microBolusAllowed = variant.smb`,
   `treatments` — журнал варианта (сид + реальные ручные болюсы/углеводы + решения варианта),
   `algorithm`, `tempTarget`, `mealMode = "announced"`.
   `tdd` — из трейса: `basal_at(now) * 24` плюс сумма ручных болюсов за окно, приведённая к суткам
   (`* 24 / часы_окна`); переопределяется `profile_overrides["tdd"]`. `isf`/`carbRatio`/`target` —
   из `profile.*_at(now)` с наложенными overrides.
8. Ответ: `rt`, `algorithm`; `smb = float(rt.get("units") or 0.0)`;
   `rate = rt.get("rate")`; `final_isf = algorithm.get("sens")`;
   `factors = (algorithm.get("aiv") or {})` — в ответе `cycle` факторы лежат внутри `algorithm`,
   потому что `cycle` возвращает результат `autoisf` как `algorithm`.
9. Журнал (формат моста): при `smb > 0` — `{"kind":"bolus","ts":now,"amount":smb,
   "type":"SMB","isValid":True}`; при `rate` не `None` — `{"kind":"temp","ts":now,
   "duration":int(duration_min*60_000),"rate":rate,"isAbsolute":True,"isValid":True}`.
   `max_iob`/`max_basal`/`SMBInterval`/`bolus_increment` — из prefs
   (`openapsmb_max_iob`, `openapsma_max_basal`, `smbinterval`, `bolus_increment`).
10. `iob` — из `response["iob"]["iob"][0]["iob"]`.

`replay_to_dict`/`replay_from_dict` — `dataclasses.asdict` и обратная сборка.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_replay_loop.py -v`
Expected: 4 passed

- [ ] **Step 5: Коммит**

```bash
git add python/runner/replay_loop.py tests/test_replay_loop.py
git commit -m "feat(replay): counterfactual decisions on a real CGM trace"
```

---

### Task 7: Сверка replay с `openaps.suggested` и отчёт W2

**Files:**
- Create: `python/golden/check_replay_parity.py`
- Modify: `python/nightscout/trace.py` (add `trace_to_dict` / `trace_from_dict`)
- Create: `docs/acceptance/W2.md`
- Test: `tests/test_replay_parity.py`

**Interfaces:**
- Consumes: `ReplayResult`, `DecisionRecord`.
- Produces:
  - `trace_to_dict(trace: DayTrace) -> dict`, `trace_from_dict(data: dict) -> DayTrace` в `trace.py`
    (round-trip `asdict`, включая вложенные `CgmPoint`/`Treatment`/`DecisionRecord`/`ProfileSegment`)
  - `DEFAULT_TOLERANCES = {"final_isf": 0.01, "insulin_req": 0.05, "rate": 0.05, "smb_on": 0.0, "factor": 0.01}`
  - `compare(replay: ReplayResult, decisions: list[DecisionRecord], tolerances: dict[str, float] = DEFAULT_TOLERANCES) -> dict[str, dict]`
  - `main(argv=None) -> int` (CLI: `--trace`, `--prefs`, `--insulin`, `--out`; при `--replay` берёт
    готовый JSON вместо нового прогона).

- [ ] **Step 1: Написать падающий тест**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from golden.check_replay_parity import DEFAULT_TOLERANCES, compare
from nightscout.trace import DecisionRecord
from runner.replay_loop import ReplayCycle, ReplayResult, ReplayVariant

T0 = 1_790_000_000_000


def decision(ts, isf=50.0, req=0.4, rate=1.1, smb=True, acce=1.0):
    return DecisionRecord(ts=ts, deliver_at=ts, insulin_req=req, final_isf=isf,
                          factors={"acceIsf": acce}, smb_on=smb, rate=rate,
                          eventual_bg=120.0, sensitivity_ratio=1.0)


def cycle(ts, isf=50.0, req=0.4, rate=1.1, smb=0.2, acce=1.0):
    return ReplayCycle(ts=ts, glucose=120.0, rate=rate, smb=smb, insulin_req=req,
                       final_isf=isf, factors={"acceIsf": acce}, iob=1.0,
                       eventual_bg=120.0, sensitivity_ratio=1.0, reason="")


def test_trace_json_round_trip():
    from nightscout.trace import build_trace, trace_from_dict, trace_to_dict
    T0 = 1_790_000_000_000
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}], treatments=[],
                        profiles=[], devicestatus=[], window_start=T0, window_end=T0 + 1)
    again = trace_from_dict(trace_to_dict(trace))
    assert again.entries == trace.entries
    assert again.window_start == T0


def test_exact_match_is_full():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0), cycle(T0 + 300_000)], [], [])
    stats = compare(replay, [decision(T0), decision(T0 + 300_000)])
    assert stats["final_isf"]["match"] == 1.0
    assert stats["rate"]["max_error"] == 0.0
    assert stats["smb_on"]["match"] == 1.0


def test_tolerance_is_fixed_and_reported():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0, isf=50.02)], [], [])
    stats = compare(replay, [decision(T0)])
    assert stats["final_isf"]["match"] == 0.0
    assert stats["final_isf"]["max_error"] == 0.02
    assert DEFAULT_TOLERANCES["final_isf"] == 0.01
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_replay_parity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'golden.check_replay_parity'`

- [ ] **Step 3: Реализовать `compare`**

Сопоставление: для каждого `decision` найти `cycle` с `abs(cycle.ts - decision.deliver_at) <= 60_000`;
если нет — счётчик `unmatched += 1`. Поля: `final_isf`, `insulin_req`, `rate`, `smb_on`
(`bool(cycle.smb > 0) == decision.smb_on`), `factor:<name>` для каждого ключа `factors`.
Для каждого — `{"n", "matched", "match", "max_error"}`. `match = matched/n`, `max_error` — максимум
абсолютной ошибки. Отчёт в Markdown: таблица поле/совпадение/макс. ошибка/порог, плюс
`unmatched` и `skipped`.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_replay_parity.py -v`
Expected: 2 passed

- [ ] **Step 5: Прогнать W2 на реальном трейсе**

```bash
.venv/bin/python -m nightscout import --url <ns-url> --token <token> --days 3 \
    --prefs ~/Downloads/<export>.json --password <password> --out out/whatif/trace.json
.venv/bin/python python/golden/check_replay_parity.py --trace out/whatif/trace.json \
    --prefs data/raw/settings.json --insulin "Lyumjev 45m 8h 100 ед" --out docs/acceptance/W2.md
```

`main` в этом режиме сам собирает baseline-вариант из prefs и запускает `run_replay` через
`Bridge()`, затем пишет отчёт и `out/whatif/replay.json`.

Пороги — ровно из `DEFAULT_TOLERANCES`. Если совпадение ниже, отчёт фиксирует это как провал W2,
а не подкручивает допуски.

- [ ] **Step 6: Коммит**

```bash
git add python/golden/check_replay_parity.py tests/test_replay_parity.py docs/acceptance/W2.md
git commit -m "feat(replay): parity check against recorded openaps decisions"
```

---

### Task 8: Model-движок, подбор субъекта и отчёт W3

**Files:**
- Create: `python/runner/whatif_model.py`
- Create: `docs/acceptance/W3.md`
- Test: `tests/test_whatif_model.py`

**Interfaces:**
- Consumes: `DayTrace`, `load_subjects`, `cached_subject_profile`, `run_closed_loop`, `Scenario`.
- Produces:
  - `PatientMatch(subject_name, distance, subject_tdi, subject_cr, subject_isf, user_tdi, user_cr, user_isf, confidence)`
  - `match_subject(subjects: dict[str, Subject], tdi: float, cr: float, isf: float) -> PatientMatch`
  - `trace_scenario(trace: DayTrace, warmup_hours: float = 8.0) -> Scenario`
  - `Scenario` получает поле `boluses: list[tuple[float, float]]` (минута, единицы)
  - `run_model(trace, prefs, insulin, variant, bridge, subject=None) -> ModelResult`
  - `ModelResult(match, cycles: list[ReplayCycle], glucose_offset: float, warnings: list[str])`

- [ ] **Step 1: Написать падающий тест**

```python
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.whatif_model import match_subject, trace_scenario


class FakeSubject:
    def __init__(self, name, cr, tdi, isf):
        self.name = name
        self.cr = cr
        self.tdi = tdi
        self.isf = isf


def test_match_subject_picks_nearest_in_log_space():
    subjects = {"a": FakeSubject("a", 10.0, 40.0, 50.0),
                "b": FakeSubject("b", 20.0, 80.0, 100.0)}
    match = match_subject(subjects, tdi=41.0, cr=10.2, isf=51.0)
    assert match.subject_name == "a"
    assert match.confidence == "good"
    assert match.distance == pytest.approx(
        math.sqrt(sum(v * v for v in (math.log(41/40), math.log(10.2/10), math.log(51/50)))), rel=1e-3
    )


def test_match_subject_marks_low_confidence():
    subjects = {"a": FakeSubject("a", 10.0, 40.0, 50.0)}
    assert match_subject(subjects, tdi=120.0, cr=30.0, isf=150.0).confidence == "low"


def test_trace_scenario_uses_real_meals_and_warmup():
    from nightscout.trace import build_trace
    T0 = 1_790_000_000_000
    treatments = [{"eventType": "Carb Correction", "date": T0 - 60 * 60_000, "carbs": 30},
                  {"eventType": "Carb Correction", "date": T0 + 60 * 60_000, "carbs": 40}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 120}],
                        treatments=treatments,
                        profiles=[{"startDate": T0 - 86_400_000, "defaultProfile": "Main",
                                   "store": {"Main": {"units": "mmol", "timezone": "UTC",
                                                      "sens": [{"time": "00:00", "value": 5.0}],
                                                      "carbratio": [{"time": "00:00", "value": 10.0}],
                                                      "basal": [{"time": "00:00", "value": 1.0}],
                                                      "target_low": [{"time": "00:00", "value": 5.5}],
                                                      "target_high": [{"time": "00:00", "value": 7.0}]}}}],
                        devicestatus=[], window_start=T0, window_end=T0 + 2 * 3600_000)
    scenario = trace_scenario(trace, warmup_hours=8.0)
    assert scenario.duration_min == 8 * 60 + 120
    assert scenario.meals[0] == (7 * 60, 30.0)
    assert scenario.meals[1] == (9 * 60, 40.0)
    assert scenario.meal_mode == "announced"
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_whatif_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.whatif_model'`

- [ ] **Step 3: Реализовать подбор и сценарий**

`match_subject`: для каждого субъекта взять `cached_subject_profile(subject, "rule")` →
`tdi`, `cr`, `isf`; расстояние — евклид в лог-пространстве; `confidence = "good"` при
`distance <= 0.35`, иначе `"low"` (порог зафиксирован до первого прогона).

`trace_scenario`: `warmup = int(warmup_hours*60)`; события до `window_start` сдвинуть на
`warmup - (window_start - ts)/60_000`, после — на `warmup + (ts - window_start)/60_000`;
`duration_min = warmup + (window_end - window_start)/60_000`; `meal_mode = "announced"` (углеводы
из Nightscout объявлены). Болюсы в warmup — **все** (manual, unknown и loop SMB), потому что это
реальная физиология до окна; в окне — только manual/unknown, иначе SMB удвоятся с решениями
модели. Добавить `boluses: list[tuple[float, float]]` в `Scenario`
(`python/runner/closed_loop.py:29-42`) и учитывать в `init_bridge`/`run_closed_loop` как
`kind="bolus"` при `ts` от `EPOCH0_MS`.

- [ ] **Step 4: Реализовать `run_model`**

Собрать `scenario`, выбрать субъекта (`subject or match_subject(...)`), затем собрать
`AlgorithmProfile` **напрямую из профиля пользователя**, а не через `profile_for`: `sens`/`carb_ratio`/
`target_bg`/`current_basal` — из `trace.profile_at(window_start)` (с `profile_overrides`), `tdd` — из
трейса, `max_iob`/`max_basal`/`smb_interval` — из prefs. Субъект нужен только для физиологии.
Вызвать `run_closed_loop` с `seed=0`, `noise_kind="none"`, `on_cycle=None`. Отчётная часть — циклы после
`warmup`; `glucose_offset = model_glucose_at(warmup) - real_glucose_at(window_start)` — **диагностика**,
не сдвиг: TIR/TBR/LBGI считаются по несдвинутой модельной глюкозе, иначе при большом offset
метрики теряют смысл. В отчёте offset выводится отдельной строкой. `ModelResult.cycles` — те же
`ReplayCycle`, что в Task 6.

- [ ] **Step 5: Запустить тесты**

Run: `pytest tests/test_whatif_model.py -v`
Expected: 3 passed

- [ ] **Step 6: Написать `docs/acceptance/W3.md` и закоммитить**

Отчёт: субъект, расстояние, confidence, offset, TIR/TBR модели против реального трейса, ограничения
(модель, не прогноз). Коммит:

```bash
git add python/runner/whatif_model.py python/runner/closed_loop.py tests/test_whatif_model.py docs/acceptance/W3.md
git commit -m "feat(whatif): model mode with cohort matching and seed history"
```

---

### Task 9: API `/api/whatif/*`

**Files:**
- Modify: `python/server/live.py`
- Test: `tests/test_whatif_api.py`

**Interfaces:**
- Consumes: `NightscoutClient`, `build_trace`, `decode_export`, `run_replay`, `run_model`.
- Produces (HTTP):
  - `POST /api/whatif/import` `{url, token, days, prefs_text?, password?}` → `{trace_id, summary}`
  - `GET /api/whatif/trace/<id>` → `summary`
  - `POST /api/whatif/run` `{trace_id, mode, variants, options}` → `{run_id}`
  - `GET /api/whatif/stream?id=<run_id>` → SSE `start|variant|cycle|done|error`
  - `DELETE /api/whatif/trace/<id>` → `{deleted: true}`

- [ ] **Step 1: Написать падающий тест**

```python
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import server.live as live


def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), live.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_import_returns_summary_without_token(monkeypatch):
    monkeypatch.setattr(live, "whatif_fetch", lambda payload: ({"entries": 3}, "trace-1"))
    server = start()
    try:
        body = json.dumps({"url": "http://ns", "token": "tok-secret", "days": 3}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/whatif/import",
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read())
        assert payload == {"trace_id": "trace-1", "summary": {"entries": 3}}
        assert "tok-secret" not in json.dumps(payload)
    finally:
        server.shutdown()


def test_unknown_trace_is_404():
    server = start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/whatif/trace/nope")
        assert exc.value.code == 404
    finally:
        server.shutdown()
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_whatif_api.py -v`
Expected: FAIL — `AttributeError: module 'server.live' has no attribute 'whatif_fetch'`

- [ ] **Step 3: Реализовать эндпоинты**

Добавить в `live.py`:

- `TRACES: dict[str, DayTrace]`, `WHATIF_STREAMS: dict[str, queue.Queue]`, `WHATIF_LOCK`.
- `whatif_fetch(payload) -> tuple[dict, str]` — создаёт клиент, тянет окно, строит trace,
  кладёт в `TRACES` под `uuid4().hex[:12]`, возвращает сводку и id. Токен и пароль — только
  локальные переменные.
- `whatif_run(spec, channel)` — выбирает `run_replay`/`run_model`, шлёт `start`, по циклу `cycle`,
  `done` с метриками.
- `do_POST`/`do_GET`/`do_DELETE` — новые пути; `do_DELETE` сейчас нет, добавить.
- `GET /api/whatif/options` — доступные варианты и ключи prefs (как `/api/options`).
- TTL: при импорте удалять трейсы старше 2 ч.

- [ ] **Step 4: Запустить тесты**

Run: `pytest tests/test_whatif_api.py -v`
Expected: 2 passed

- [ ] **Step 5: Коммит**

```bash
git add python/server/live.py tests/test_whatif_api.py
git commit -m "feat(server): what-if import and run endpoints"
```

---

### Task 10: Вкладка UI, Docker и отчёт W4

**Files:**
- Create: `web/whatif.html`
- Create: `web/whatif.js`
- Create: `web/whatif.css`
- Modify: `web/i18n.js`
- Modify: `web/index.html` (ссылка на вкладку)
- Modify: `Dockerfile` (если порт/зависимость)
- Create: `docs/acceptance/W4.md`
- Test: `tests/test_whatif_ui.py`

**Interfaces:**
- Consumes: `/api/whatif/*` из Task 9.
- Produces: страница `whatif.html` с шагами «подключение → режим → варианты → результат».

- [ ] **Step 1: Написать падающий статический тест**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_whatif_page_wires_assets_and_labels():
    html = (ROOT / "web/whatif.html").read_text(encoding="utf-8")
    assert 'src="whatif.js' in html
    assert 'href="whatif.css' in html
    assert 'data-i18n="whatif.connect"' in html
    assert 'id="variant-list"' in html
    assert 'id="chart"' in html
    assert 'data-i18n="whatif.warning.notPrediction"' in html


def test_i18n_has_whatif_keys_in_both_languages():
    text = (ROOT / "web/i18n.js").read_text(encoding="utf-8")
    for key in ("whatif.connect", "whatif.mode.replay", "whatif.mode.model",
                "whatif.variants", "whatif.warning.notPrediction"):
        assert text.count(f'"{key}"') >= 2
```

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `pytest tests/test_whatif_ui.py -v`
Expected: FAIL — `FileNotFoundError: web/whatif.html`

- [ ] **Step 3: Реализовать страницу**

`whatif.html`: форма подключения (URL, токен, дни, файл экспорта, пароль), переключатель
`Replay | Model`, редактор вариантов (алгоритм, SMB, веса), кнопка «запустить», SVG-график,
таблица метрик, блок «что не воспроизведено». Токен и пароль — `type="password"`, не сохраняются
в `localStorage`.

`whatif.js`: `fetch('/api/whatif/import')`, затем `EventSource('/api/whatif/stream?id=...')`;
рисует глюкозу (реальную линию всегда, модельную — только в model), инсулин/SMB, IOB; таблица
вариантов; плашка «это не клинический прогноз».

`i18n.js`: ключи RU/EN.

- [ ] **Step 4: Запустить тесты и ручной чек-лист**

```bash
pytest tests/test_whatif_ui.py -v
python3 python/server/live.py --port 8766
```

Чек-лист: импорт синтетического трейса; переключение режимов; 3 варианта; удаление импорта;
токен не виден в Network/Console; при `smb: off` SMB не растёт.

- [ ] **Step 5: Написать `docs/acceptance/W4.md` и закоммитить**

```bash
git add web/whatif.html web/whatif.js web/whatif.css web/i18n.js web/index.html tests/test_whatif_ui.py docs/acceptance/W4.md
git commit -m "feat(web): what-if tab with replay and model modes"
```

---

## Self-Review

- Покрытие спеки: импорт (Task 1–4), расшифровка настроек (Task 2), TT/profile switch/autosens
  (Task 3, 6), replay и W2 (Task 6–7), model и W3 (Task 8), API (Task 9), UI и W4 (Task 10).
  Пункт «SMB-доля без пересчёта по каденции» — Task 6, шаг 7.
- Типы: `ReplayCycle` используется и в model (Task 8), `DayTrace` — везде; `ReplayVariant` —
  в API и UI.
- Review Focus: утечка секрета (Task 1/4), двойной SMB (Task 3/6), короткий temp basal (Task 3/6),
  окно TT (Task 3/6), ложные метрики (Task 9/10).
- Открытых вопросов нет: 3 дня, TT воспроизводятся, настройки — зашифрованный экспорт.
