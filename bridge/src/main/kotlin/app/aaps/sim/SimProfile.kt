package app.aaps.sim

import app.aaps.core.data.model.GlucoseUnit
import app.aaps.core.data.model.ICfg
import app.aaps.core.interfaces.profile.EffectiveProfile
import java.lang.reflect.Proxy

class BasalTimeline(rows: List<Pair<Long, Double>>) {

    private val timestamps: LongArray
    private val rates: DoubleArray

    init {
        val sorted = rows.sortedBy { it.first }
        timestamps = LongArray(sorted.size) { sorted[it].first }
        rates = DoubleArray(sorted.size) { sorted[it].second }
    }

    fun at(timestamp: Long): Double {
        if (timestamps.isEmpty()) return 0.0
        var low = 0
        var high = timestamps.size - 1
        if (timestamp < timestamps[0]) return rates[0]
        while (low < high) {
            val mid = (low + high + 1) / 2
            if (timestamps[mid] <= timestamp) low = mid else high = mid - 1
        }
        return rates[low]
    }
}

fun simEffectiveProfile(
    basal: (Long) -> Double,
    currentBasal: Double,
    iCfg: ICfg,
    isfMgdl: Double,
    carbRatio: Double,
    targetLow: Double,
    targetHigh: Double,
): EffectiveProfile = Proxy.newProxyInstance(
    EffectiveProfile::class.java.classLoader,
    arrayOf(EffectiveProfile::class.java),
) { _, method, args ->
    when (method.name) {
        "getIcfg", "getICfg" -> iCfg
        "getPercentage" -> 100
        "getTimeshift" -> 0
        "getUnits" -> GlucoseUnit.MGDL
        "getBasal" -> when {
            args != null && args.isNotEmpty() && args[0] is Long -> basal(args[0] as Long)
            else -> currentBasal
        }
        "getBasalTimeFromMidnight" -> currentBasal
        "getProfileIsfMgdl", "getIsfMgdl", "getAverageIsfMgdl" -> isfMgdl
        "getIsfMgdlForCarbs", "getIsfMgdlTimeFromMidnight" -> isfMgdl
        "getIc", "getIcTimeFromMidnight" -> carbRatio
        "getTargetMgdl", "getRoundedTargetMgdl" -> (targetLow + targetHigh) / 2.0
        "getTargetLowMgdl", "getTargetLowMgdlTimeFromMidnight" -> targetLow
        "getTargetHighMgdl", "getTargetHighMgdlTimeFromMidnight" -> targetHigh
        "isEqual", "equals" -> false
        "hashCode" -> System.identityHashCode(args)
        "toString" -> "SimProfile"
        else -> defaultValue(method.returnType)
    }
} as EffectiveProfile
