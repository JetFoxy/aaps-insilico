package app.aaps.sim

import app.aaps.core.data.model.BS
import app.aaps.core.data.model.ICfg
import app.aaps.core.data.model.TB
import app.aaps.core.data.model.iobCalc
import app.aaps.core.interfaces.aps.AutosensResult
import app.aaps.core.interfaces.aps.IobTotal
import app.aaps.core.interfaces.profile.EffectiveProfile
import app.aaps.core.objects.extensions.combine
import app.aaps.core.objects.extensions.iobCalc as tbIobCalc
import app.aaps.core.objects.extensions.plus
import app.aaps.core.objects.extensions.round

data class BolusRecord(
    val timestamp: Long,
    val amount: Double,
    val type: BS.Type,
    val iCfg: ICfg,
    val isValid: Boolean = true,
)

data class TempRecord(
    val timestamp: Long,
    val durationMs: Long,
    val rate: Double,
    val isAbsolute: Boolean,
    val isValid: Boolean = true,
)

class IobBuilder(
    private val bolusSnoozeDivisor: Double = 1.5,
    private val lookbackMs: Long = 12L * 60 * 60 * 1000,
) {

    fun array(
        now: Long,
        boluses: List<BolusRecord>,
        temps: List<TempRecord>,
        profileFor: (Long) -> EffectiveProfile,
        autosensRatio: Double,
        exerciseMode: Boolean,
        halfBasalExerciseTarget: Int,
        isTempTarget: Boolean,
        steps: Int = 48,
    ): Array<IobTotal> = Array(steps) { index ->
        calculateAt(
            now = now,
            time = now + index * 5L * 60 * 1000,
            boluses = boluses,
            temps = temps,
            profileFor = profileFor,
            autosensRatio = autosensRatio,
            exerciseMode = exerciseMode,
            halfBasalExerciseTarget = halfBasalExerciseTarget,
            isTempTarget = isTempTarget,
        )
    }

    fun totalAt(time: Long, profileFor: (Long) -> EffectiveProfile, boluses: List<BolusRecord>, temps: List<TempRecord>): IobTotal {
        val bolusIob = bolusIob(time, boluses).round()
        val basalIob = basalIob(time, time, temps, profileFor, 1.0, false, 160, false).round()
        return IobTotal.combine(bolusIob, basalIob).round()
    }

    private fun calculateAt(
        now: Long,
        time: Long,
        boluses: List<BolusRecord>,
        temps: List<TempRecord>,
        profileFor: (Long) -> EffectiveProfile,
        autosensRatio: Double,
        exerciseMode: Boolean,
        halfBasalExerciseTarget: Int,
        isTempTarget: Boolean,
    ): IobTotal {
        val knownBoluses = boluses.filter { it.timestamp <= now }
        val knownTemps = temps.filter { it.timestamp <= now }
        val bolusIob = bolusIob(time, knownBoluses).round()
        val basalIob = basalIob(now, time, knownTemps, profileFor, autosensRatio, exerciseMode, halfBasalExerciseTarget, isTempTarget).round()

        val basalWithZeroTemp = basalIob.copy()
        if (now + 60_000L < time) {
            val profile = profileFor(now)
            run {
                val zero = TB(
                    timestamp = now + 60_000L,
                    duration = 240L * 60 * 1000,
                    rate = 0.0,
                    isAbsolute = true,
                    type = TB.Type.NORMAL,
                )
                basalWithZeroTemp.plus(
                    zero.tbIobCalc(time, profile, AutosensResult(ratio = autosensRatio), exerciseMode, halfBasalExerciseTarget, isTempTarget)
                )
            }
        }
        basalIob.iobWithZeroTemp = IobTotal.combine(bolusIob, basalWithZeroTemp).round()
        return IobTotal.combine(bolusIob, basalIob).round()
    }

    private fun bolusIob(time: Long, boluses: List<BolusRecord>): IobTotal {
        val total = IobTotal(time)
        if (!bolusSnoozeDivisor.isFinite() || bolusSnoozeDivisor <= 0.0) return total
        boluses.forEach { record ->
            if (!record.isValid || record.timestamp >= time || record.timestamp < time - lookbackMs) return@forEach
            val bs = BS(
                timestamp = record.timestamp,
                amount = record.amount,
                type = record.type,
                iCfg = record.iCfg,
            )
            val contrib = bs.iobCalc(time)
            total.iob += contrib.iobContrib
            total.activity += contrib.activityContrib
            if (record.amount > 0 && record.timestamp > total.lastBolusTime) total.lastBolusTime = record.timestamp
            if (record.type != BS.Type.SMB) {
                val timeSinceTreatment = time - record.timestamp
                val snoozeTime = record.timestamp + (timeSinceTreatment * bolusSnoozeDivisor).toLong()
                total.bolussnooze += bs.iobCalc(snoozeTime).iobContrib
            }
        }
        return total
    }

    private fun basalIob(
        now: Long,
        time: Long,
        rawTemps: List<TempRecord>,
        profileFor: (Long) -> EffectiveProfile,
        autosensRatio: Double,
        exerciseMode: Boolean,
        halfBasalExerciseTarget: Int,
        isTempTarget: Boolean,
    ): IobTotal {
        val total = IobTotal(time)
        val temps = truncateSuperseded(rawTemps)
        temps.forEach { record ->
            if (!record.isValid || record.timestamp > time || record.timestamp < time - lookbackMs) return@forEach
            val profile = profileFor(record.timestamp)
            val end = record.timestamp + record.durationMs
            val effectiveDuration = if (end > now) now - record.timestamp else record.durationMs
            if (effectiveDuration <= 0) return@forEach
            val tb = TB(
                timestamp = record.timestamp,
                duration = effectiveDuration,
                rate = record.rate,
                isAbsolute = record.isAbsolute,
                type = TB.Type.NORMAL,
            )
            total.plus(
                tb.tbIobCalc(time, profile, AutosensResult(ratio = autosensRatio), exerciseMode, halfBasalExerciseTarget, isTempTarget)
            )
        }
        return total
    }

    private fun truncateSuperseded(temps: List<TempRecord>): List<TempRecord> {
        val sorted = temps.sortedBy { it.timestamp }
        return sorted.mapIndexed { index, record ->
            val next = sorted.getOrNull(index + 1)
            val end = if (next != null && next.timestamp < record.timestamp + record.durationMs) next.timestamp - record.timestamp else record.durationMs
            if (end > 0 && end != record.durationMs) record.copy(durationMs = end) else record
        }
    }
}
