# Происхождение `vpatient_params.csv`

- Файл: `python/simcore/params/vpatient_params.csv`
- Источник: репозиторий `jxx123/simglucose`, путь `simglucose/params/vpatient_params.csv`,
  ветка `master`, скачан 2026-11-10.
- Что это: 30 векторов параметров (10 взрослых, 10 подростков, 10 детей) модели S2008/S2013
  UVA/Padova в открытой реализации simglucose (MIT).
- Чем это **не** является: это не официальная популяция S2017 и не значения из статьи.
  Вектор S2017 проприетарен (T1DMS); см. `docs/registry/parameters.md` §3.3 и
  `docs/acceptance/M0.md`.
- В отчётах эти параметры помечаются `derived`, а модель называется
  «UVA/Padova-подобная по Visentin et al. 2018», не «UVA/Padova simulator».

Колонки: `Name`, `i`, `x0_ 1..x0_13` (начальное состояние), далее параметры модели
(`BW`, `Gb`, `Ib`, `kabs`, `kmax`, `kmin`, `b`, `d`, `Vg`, `Vi`, `Vmx`, `Km0`, `k1`, `k2`,
`p2u`, `m1`, `m2`, `m4`, `m30`, `ki`, `kp1`, `kp2`, `kp3`, `f`, `Fsnc`, `Vm0`, `ke1`, `ke2`,
`kd`, `ka1`, `ka2`, `ksc`, `u2ss`, `isc1ss`, `isc2ss`).
