package app.aaps.sim

import app.aaps.core.data.model.EPS
import app.aaps.core.data.model.GlucoseUnit
import app.aaps.core.data.model.ICfg
import app.aaps.core.data.model.data.Block
import app.aaps.core.data.model.data.TargetBlock
import app.aaps.core.objects.profile.ProfileSealed
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

private const val DAY_MS = 24L * 60 * 60 * 1000

fun JsonObject.num(name: String, default: Double = 0.0): Double =
    (this[name] as? JsonPrimitive)?.content?.toDoubleOrNull() ?: default

fun JsonObject.int(name: String, default: Int = 0): Int =
    (this[name] as? JsonPrimitive)?.content?.toIntOrNull() ?: default

fun JsonObject.long(name: String, default: Long = 0L): Long =
    (this[name] as? JsonPrimitive)?.content?.toLongOrNull() ?: default

fun JsonObject.bool(name: String, default: Boolean = false): Boolean =
    (this[name] as? JsonPrimitive)?.content?.toBooleanStrictOrNull() ?: default

fun JsonObject.str(name: String): String? = (this[name] as? JsonPrimitive)?.content

fun JsonObject.obj(name: String): JsonObject? = this[name] as? JsonObject

fun JsonObject.arr(name: String): JsonArray? = this[name] as? JsonArray

fun JsonElement?.asDouble(): Double? = (this as? JsonPrimitive)?.content?.toDoubleOrNull()

class ProfileHistory(private val rows: List<ProfileRow>) {

    fun at(timestamp: Long): ProfileRow? = rows.lastOrNull { it.timestamp <= timestamp } ?: rows.firstOrNull()

    fun rowCount(): Int = rows.size

    companion object {

        fun fromJson(array: JsonArray): ProfileHistory {
            val rows = array.mapNotNull { element ->
                val obj = element as? JsonObject ?: return@mapNotNull null
                ProfileRow(
                    timestamp = obj.long("ts"),
                    profile = obj.str("profile") ?: "P?",
                    units = obj.str("units") ?: "mg/dl",
                    timezone = obj.str("timezone"),
                    basal = blocks(obj.arr("basal")),
                    isf = blocks(obj.arr("sens")),
                    ic = blocks(obj.arr("carbratio")),
                    targetLow = blocks(obj.arr("target_low")),
                    targetHigh = blocks(obj.arr("target_high")),
                )
            }.sortedBy { it.timestamp }
            return ProfileHistory(rows)
        }

        private fun blocks(array: JsonArray?): List<Pair<Int, Double>> {
            if (array == null) return emptyList()
            return array.mapNotNull { element ->
                val obj = element as? JsonObject ?: return@mapNotNull null
                val seconds = obj.int("timeAsSeconds")
                val value = obj.num("value")
                seconds to value
            }
        }
    }
}

data class ProfileRow(
    val timestamp: Long,
    val profile: String,
    val units: String,
    val timezone: String?,
    val basal: List<Pair<Int, Double>>,
    val isf: List<Pair<Int, Double>>,
    val ic: List<Pair<Int, Double>>,
    val targetLow: List<Pair<Int, Double>>,
    val targetHigh: List<Pair<Int, Double>>,
) {

    val glucoseUnit: GlucoseUnit
        get() = if (units.equals("mmol", ignoreCase = true)) GlucoseUnit.MMOL else GlucoseUnit.MGDL

    fun effectiveProfile(iCfg: ICfg): ProfileSealed.EPS =
        ProfileSealed.EPS(
            EPS(
                timestamp = timestamp,
                basalBlocks = toBlocks(basal, 1.0),
                isfBlocks = toBlocks(isf, 1.0),
                icBlocks = toBlocks(ic, 1.0),
                targetBlocks = toTargetBlocks(),
                glucoseUnit = glucoseUnit,
                originalProfileName = profile,
                originalCustomizedName = profile,
                originalTimeshift = 0,
                originalPercentage = 100,
                originalDuration = 0,
                originalEnd = 0,
                iCfg = iCfg,
            ),
            null,
        )

    private fun toMgdl(value: Double): Double = if (glucoseUnit == GlucoseUnit.MMOL) value * 18.0 else value

    private fun toTargetBlocks(): List<TargetBlock> {
        val lows = toBlocks(targetLow, 1.0)
        val highs = toBlocks(targetHigh, 1.0)
        if (lows.size != highs.size) throw IllegalArgumentException("target low/high block count differs in ${profile}")
        return lows.indices.map { i ->
            TargetBlock(duration = lows[i].duration, lowTarget = toMgdl(lows[i].amount), highTarget = toMgdl(highs[i].amount))
        }
    }

    private fun toBlocks(values: List<Pair<Int, Double>>, factor: Double): List<Block> {
        if (values.isEmpty()) return emptyList()
        return values.mapIndexed { index, pair ->
            val start = pair.first
            val next = if (index + 1 < values.size) values[index + 1].first else 24 * 3600
            val duration = ((next - start).toLong() * 1000L).let { if (it <= 0) DAY_MS else it }
            Block(duration = duration, amount = pair.second * factor)
        }
    }
}

fun ICfg.Companion.fromJson(obj: JsonObject): ICfg = ICfg(
    insulinLabel = obj.str("label") ?: obj.str("insulinLabel") ?: "unknown",
    insulinEndTime = obj.long("endTime").takeIf { it != 0L } ?: obj.long("insulinEndTime"),
    insulinPeakTime = obj.long("peakTime").takeIf { it != 0L } ?: obj.long("insulinPeakTime"),
    concentration = obj.num("concentration", 1.0),
)
