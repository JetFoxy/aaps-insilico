package app.aaps.sim

import app.aaps.core.data.model.BS
import app.aaps.core.data.model.TT
import app.aaps.core.interfaces.aps.AutosensResult
import app.aaps.core.interfaces.aps.CurrentTemp
import app.aaps.core.interfaces.aps.GlucoseStatusAutoIsf
import app.aaps.core.interfaces.aps.IobTotal
import app.aaps.core.interfaces.aps.MealData
import app.aaps.core.interfaces.aps.OapsProfileAutoIsf
import app.aaps.core.data.iob.InMemoryGlucoseValue
import app.aaps.core.data.model.EPS
import app.aaps.core.data.model.GlucoseUnit
import app.aaps.core.data.model.data.Block
import app.aaps.core.data.model.data.TargetBlock
import app.aaps.core.data.model.GV
import app.aaps.core.data.model.SourceSensor
import app.aaps.core.data.model.TrendArrow
import app.aaps.core.data.model.advancedFilteringSupported
import app.aaps.core.keys.interfaces.Preferences
import app.aaps.plugins.aps.openAPSAutoISF.DetermineBasalAutoISF
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.lang.reflect.Field
import java.time.ZoneId
import java.util.TimeZone
import kotlin.system.exitProcess

private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

class Bridge {

    private val clock = SimClock()
    private val logger = ConsoleLogger(verbose = System.getenv("SIM_VERBOSE") == "1")
    private val dateUtil = dateUtilProxy(clock)
    private lateinit var preferences: Preferences
    private var preferenceValues: MutableMap<String, Any?> = mutableMapOf()
    private lateinit var profiles: ProfileHistory
    private lateinit var iCfg: app.aaps.core.data.model.ICfg
    private var basal = BasalTimeline(emptyList())
    private lateinit var pipeline: GlucosePipeline
    private lateinit var autoIsf: AutoIsfRunner
    private var boluses: List<BolusRecord> = emptyList()
    private var temps: List<TempRecord> = emptyList()
    private var carbs: List<app.aaps.core.data.model.CA> = emptyList()
    private lateinit var iobCalculator: SimIobCobCalculator
    private var bolusSnoozeDivisor: Double = 1.5
    private val determineBasal = DetermineBasalAutoISF(profileUtilProxy())

    fun handle(line: String): JsonObject {
        val request = json.parseToJsonElement(line).jsonObject
        return when (val cmd = request.str("cmd")) {
            "init" -> init(request)
            "glucose" -> glucose(request)
            "iob" -> iob(request)
            "autoisf" -> autoisf(request)
            "autoisfStatus" -> autoisfStatus(request)
            "dynisf" -> dynisf(request)
            "meal" -> meal(request)
            "step" -> step(request)
            "ukfReset" -> {
                pipeline.resetLearning()
                buildJsonObject { put("ok", true) }
            }
            "prefGet" -> buildJsonObject {
                val key = request.str("key") ?: error("key is required")
                put("key", key)
                put("present", preferenceValues.containsKey(key))
                put("value", preferenceValues[key]?.toString() ?: "null")

            }
            "pref" -> {
                preferenceValues[request.str("key") ?: error("key is required")] = request["value"]?.toKotlinValue()
                buildJsonObject { put("ok", true) }
            }
            "selftest" -> JsonObject(mapOf("rt" to json.parseToJsonElement(selftest())))
            else -> error("unknown command: $cmd")
        }
    }

    private fun init(request: JsonObject): JsonObject {
        val timezone = request.str("timezone")
        if (timezone != null) TimeZone.setDefault(TimeZone.getTimeZone(ZoneId.of(timezone)))

        val prefs = request.obj("prefs") ?: JsonObject(emptyMap())
        val values = prefs.mapValues { (_, value) -> value.toKotlinValue() }.toMutableMap()
        preferenceValues = values
        val simPreferences = SimPreferences(values)
        if (request["ukfLearnedR"] != null) values["ukf_learned_r"] = request.num("ukfLearnedR")
        preferences = simPreferences.asPreferences()

        profiles = ProfileHistory.fromJson(request.arr("profiles") ?: JsonArray(emptyList()))
        iCfg = app.aaps.core.data.model.ICfg.fromJson(request.obj("insulin") ?: error("insulin is required"))
        bolusSnoozeDivisor = request.num("bolusSnoozeDivisor", 1.5)
        val basalRows = request.arr("basal")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val rate = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            ts to rate
        } ?: emptyList()
        basal = BasalTimeline(basalRows)

        pipeline = GlucosePipeline(preferences, dateUtil, clock, logger)
        autoIsf = AutoIsfRunner(preferences, dateUtil, clock, logger, pipeline)
        determineBasal.preferences = preferences
        iobCalculator = SimIobCobCalculator(
            ads = pipeline.store,
            iobBuilder = IobBuilder(bolusSnoozeDivisor),
            boluses = { boluses },
            temps = { temps },
            mealAt = { app.aaps.core.interfaces.aps.MealData() },
            profileFor = { ts ->
                simEffectiveProfile(
                    basal = { basal.at(it) },
                    currentBasal = basal.at(ts),
                    iCfg = iCfg,
                    isfMgdl = 100.0,
                    carbRatio = 10.0,
                    targetLow = 100.0,
                    targetHigh = 100.0,
                )
            },
        )

        val treatments = request.arr("treatments") ?: JsonArray(emptyList())
        parseTreatments(treatments)

        return buildJsonObject {
            put("ok", true)
            put("prefs", values.size)
            put("profiles", profiles.rowCount())
            put("boluses", boluses.size)
            put("temps", temps.size)
            put("carbs", carbs.size)
        }
    }

    private fun parseTreatmentLists(array: JsonArray): Pair<List<BolusRecord>, List<TempRecord>> {
        val bolusList = mutableListOf<BolusRecord>()
        val tempList = mutableListOf<TempRecord>()
        array.forEach { element ->
            val treatment = element as? JsonObject ?: return@forEach
            when (treatment.str("kind")) {
                "bolus" -> bolusList += BolusRecord(
                    timestamp = treatment.long("ts"),
                    amount = treatment.num("amount"),
                    type = BS.Type.fromString(treatment.str("type")),
                    iCfg = treatment.obj("icfg")?.let { app.aaps.core.data.model.ICfg.fromJson(it) } ?: iCfg,
                    isValid = treatment.bool("isValid", true),
                )
                "temp" -> tempList += TempRecord(
                    timestamp = treatment.long("ts"),
                    durationMs = treatment.long("duration"),
                    rate = treatment.num("rate"),
                    isAbsolute = treatment.bool("isAbsolute", true),
                    isValid = treatment.bool("isValid", true),
                )
            }
        }
        return bolusList.sortedBy { it.timestamp } to tempList.sortedBy { it.timestamp }
    }

    private fun parseTreatments(array: JsonArray) {
        val bolusList = mutableListOf<BolusRecord>()
        val tempList = mutableListOf<TempRecord>()
        val carbList = mutableListOf<app.aaps.core.data.model.CA>()
        array.forEach { element ->
            val treatment = element as? JsonObject ?: return@forEach
            when (treatment.str("kind")) {
                "bolus" -> bolusList += BolusRecord(
                    timestamp = treatment.long("ts"),
                    amount = treatment.num("amount"),
                    type = BS.Type.fromString(treatment.str("type")),
                    iCfg = treatment.obj("icfg")?.let { app.aaps.core.data.model.ICfg.fromJson(it) } ?: iCfg,
                    isValid = treatment.bool("isValid", true),
                )
                "temp" -> tempList += TempRecord(
                    timestamp = treatment.long("ts"),
                    durationMs = treatment.long("duration"),
                    rate = treatment.num("rate"),
                    isAbsolute = treatment.bool("isAbsolute", true),
                    isValid = treatment.bool("isValid", true),
                )
                "carbs" -> treatment.num("amount").takeIf { it > 0 }?.let { amount ->
                    carbList += app.aaps.core.data.model.CA(
                        timestamp = treatment.long("ts"),
                        duration = treatment.long("duration"),
                        amount = amount,
                    )
                }
            }
        }
        boluses = bolusList.sortedBy { it.timestamp }
        temps = tempList.sortedBy { it.timestamp }
        carbs = carbList.sortedBy { it.timestamp }
    }

    private fun glucose(request: JsonObject): JsonObject {
        val now = request.long("now")
        val readings = request.arr("readings")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val value = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            ts to value
        } ?: emptyList()
        val source = SourceSensor.fromString(request.str("sourceSensor") ?: "Libre2")
        val status = pipeline.glucose(now, readings, source)
        return buildJsonObject {
            if (status == null) put("glucose", JsonNull)
            else put("glucose", status.toJson())
            val orig = pipeline.store.bgReadings
            put("debug", buildJsonObject {
                put("origSize", orig.size)
                put("trusted", orig.firstOrNull()?.sourceSensor?.advancedFilteringSupported() ?: false)
                put("spacingSec", if (orig.size > 2) (orig[0].timestamp - orig[2].timestamp) / 2000.0 else -1.0)
                put("newest", orig.firstOrNull()?.timestamp ?: 0L)
            })
        }
    }

    private fun iob(request: JsonObject): JsonObject {
        val now = request.long("now")
        val ratio = request.num("autosens", 1.0)
        val override = request.arr("treatments")
        val (bolusList, tempList) = if (override == null) boluses to temps else parseTreatmentLists(override)
        val profileFor: (Long) -> app.aaps.core.interfaces.profile.EffectiveProfile = { ts ->
            simEffectiveProfile(
                basal = { basal.at(it) },
                currentBasal = basal.at(ts),
                iCfg = iCfg,
                isfMgdl = 0.0,
                carbRatio = 0.0,
                targetLow = 0.0,
                targetHigh = 0.0,
            )
        }
        val array = IobBuilder(bolusSnoozeDivisor).array(
            now = now,
            boluses = bolusList,
            temps = tempList,
            profileFor = profileFor,
            autosensRatio = ratio,
            exerciseMode = request.bool("exerciseMode"),
            halfBasalExerciseTarget = request.int("halfBasalExerciseTarget", 160),
            isTempTarget = request.bool("isTempTarget"),
        )
        return buildJsonObject {
            put("iob", buildJsonArray { array.forEach { add(it.toJson()) } })
        }
    }

    private fun meal(request: JsonObject): JsonObject {
        val now = request.long("now")
        val readings = request.arr("readings")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val value = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            ts to value
        } ?: emptyList()
        val source = SourceSensor.fromString(request.str("sourceSensor") ?: "Libre2")
        pipeline.glucose(now, readings, source)
        iobCalculator.ads = pipeline.store
        val profile = simEffectiveProfile(
            basal = { basal.at(it) },
            currentBasal = request.num("currentBasal", basal.at(now)),
            iCfg = iCfg,
            isfMgdl = request.num("isf", 100.0),
            carbRatio = request.num("carbRatio", 10.0),
            targetLow = request.num("targetLow", 100.0),
            targetHigh = request.num("targetHigh", 100.0),
        )
        val data = CobRunner(preferences, dateUtil, logger, iobCalculator, profile, carbs).mealData(now)
        return buildJsonObject {
            put("carbs", data.carbs)
            put("mealCOB", data.mealCOB)
            put("slopeFromMaxDeviation", data.slopeFromMaxDeviation)
            put("slopeFromMinDeviation", data.slopeFromMinDeviation)
            put("usedMinCarbsImpact", data.usedMinCarbsImpact)
            put("lastBolusTime", boluses.lastOrNull { it.timestamp <= now }?.timestamp ?: 0L)
            put("lastCarbTime", carbs.lastOrNull { it.timestamp <= now }?.timestamp ?: 0L)
        }
    }

    private fun autoisf(request: JsonObject): JsonObject {
        val now = request.long("now")
        val readings = request.arr("readings")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val value = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            ts to value
        } ?: emptyList()
        val source = SourceSensor.fromString(request.str("sourceSensor") ?: "Libre2")
        pipeline.glucose(now, readings, source)
        val effective = simEffectiveProfile(
            basal = { basal.at(it) },
            currentBasal = request.num("currentBasal", basal.at(now)),
            iCfg = iCfg,
            isfMgdl = request.num("isf", 0.0),
            carbRatio = request.num("carbRatio", 1.0),
            targetLow = request.num("targetLow", request.num("target", 100.0)),
            targetHigh = request.num("targetHigh", request.num("target", 100.0)),
        )
        val tempTarget = request.obj("tempTarget")?.let { tt ->
            TT(
                timestamp = tt.long("ts"),
                reason = TT.Reason.fromString(tt.str("reason")),
                highTarget = tt.num("high"),
                lowTarget = tt.num("low"),
                duration = tt.long("duration"),
            )
        }
        val sens = autoIsf.autoIsf(now, effective, tempTarget)
        val status = pipeline.calculator.getGlucoseStatusData(allowOldData = false)
        return buildJsonObject {
            put("sens", sens)
            put("status", status?.toJson() ?: JsonNull)
            put("console", buildJsonArray { autoIsf.console().forEach { add(JsonPrimitive(it)) } })
        }
    }

    private fun autoisfStatus(request: JsonObject): JsonObject {
        val now = request.long("now")
        val statusData = request.arr("statusData")?.mapNotNull { element ->
            val obj = element as? JsonObject ?: return@mapNotNull null
            val value = obj.num("glucose")
            InMemoryGlucoseValue(
                timestamp = obj.long("ts"),
                value = value,
                smoothed = obj["smoothed"]?.jsonPrimitive?.doubleOrNull ?: value,
                calibrated = obj["calibrated"]?.jsonPrimitive?.doubleOrNull,
                filledGap = obj.bool("filledGap", false),
            )
        }?.toMutableList() ?: mutableListOf()
        pipeline.store.statusData = statusData
        val readings = request.arr("readings")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val value = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            GV(
                timestamp = ts,
                raw = value,
                value = value,
                trendArrow = TrendArrow.NONE,
                noise = null,
                sourceSensor = SourceSensor.fromString(request.str("sourceSensor") ?: "Libre2"),
            )
        }?.sortedByDescending { it.timestamp } ?: emptyList()
        pipeline.store.bgReadings = readings
        val effective = simEffectiveProfile(
            basal = { basal.at(it) },
            currentBasal = request.num("currentBasal", basal.at(now)),
            iCfg = iCfg,
            isfMgdl = request.num("isf", 0.0),
            carbRatio = request.num("carbRatio", 1.0),
            targetLow = request.num("targetLow", request.num("target", 100.0)),
            targetHigh = request.num("targetHigh", request.num("target", 100.0)),
        )
        val sens = autoIsf.autoIsf(now, effective, null)
        val status = pipeline.calculator.getGlucoseStatusData(allowOldData = true)
        return buildJsonObject {
            put("sens", sens)
            put("status", status?.toJson() ?: JsonNull)
            put("console", buildJsonArray { autoIsf.console().forEach { add(JsonPrimitive(it)) } })
        }
    }

    private fun dynisf(request: JsonObject): JsonObject {
        val now = request.long("now")
        val readings = request.arr("readings")?.mapNotNull { element ->
            val pair = element as? JsonArray ?: return@mapNotNull null
            val ts = pair.getOrNull(0)?.jsonPrimitive?.long ?: return@mapNotNull null
            val value = pair.getOrNull(1)?.jsonPrimitive?.double ?: return@mapNotNull null
            ts to value
        } ?: emptyList()
        val source = SourceSensor.fromString(request.str("sourceSensor") ?: "Libre2")
        pipeline.glucose(now, readings, source)
        val dayMs = 24L * 60 * 60 * 1000
        val currentBasal = request.num("currentBasal", basal.at(now))
        val isf = request.num("isf", 0.0)
        val carbRatio = request.num("carbRatio", 1.0)
        val target = request.num("target", 100.0)
        val eps = app.aaps.core.objects.profile.ProfileSealed.EPS(
            EPS(
                timestamp = now,
                basalBlocks = listOf(Block(duration = dayMs, amount = currentBasal)),
                isfBlocks = listOf(Block(duration = dayMs, amount = isf)),
                icBlocks = listOf(Block(duration = dayMs, amount = carbRatio)),
                targetBlocks = listOf(TargetBlock(duration = dayMs, lowTarget = target, highTarget = target)),
                glucoseUnit = GlucoseUnit.MGDL,
                originalProfileName = "sim",
                originalCustomizedName = "sim",
                originalTimeshift = 0,
                originalPercentage = 100,
                originalDuration = 0,
                originalEnd = 0,
                iCfg = iCfg,
            ),
            null,
        )
        val runner = DynIsfRunner(preferences, dateUtil, clock, logger, pipeline, request.num("tdd", 40.0))
        val sens = runner.sens(now, eps)
        return buildJsonObject {
            put("sens", sens?.let { JsonPrimitive(it) } ?: JsonNull)
            put("profile_isf", JsonPrimitive(eps.getProfileIsfMgdl()))
            put("profile_target", JsonPrimitive(eps.getTargetMgdl()))
            put("status_glucose", runner.lastStatusGlucose?.let { JsonPrimitive(it) } ?: JsonNull)
        }
    }

    private fun step(request: JsonObject): JsonObject {
        val input = request.obj("input") ?: error("input is required")
        val rt = determineBasal.determine_basal(
            glucose_status = input.obj("glucoseStatus")!!.toGlucoseStatus(),
            currenttemp = input.obj("currentTemp")!!.toCurrentTemp(),
            iob_data_array = input.arr("iob")!!.map { it.jsonObject.toIobTotal() }.toTypedArray(),
            profile = input.obj("profile")!!.toOapsProfile(),
            autosens_data = input.obj("autosens")?.toAutosens() ?: AutosensResult(),
            meal_data = input.obj("meal")?.toMealData() ?: MealData(),
            microBolusAllowed = input.bool("microBolusAllowed"),
            currentTime = input.long("now"),
            flatBGsDetected = input.bool("flatBGs"),
            autoIsfMode = input.bool("autoIsfMode"),
            loop_wanted_smb = input.str("loopWantedSmb") ?: "AAPS",
            profile_percentage = input.int("profilePercentage", 100),
            smb_ratio = input.num("smbRatio", 0.5),
            smb_max_range_extension = input.num("smbMaxRangeExtension", 1.0),
            iob_threshold_percent = input.int("iobThresholdPercent", 100),
            activity_consoleLog = input.str("activityConsoleLog") ?: "",
            auto_isf_consoleError = mutableListOf(),
            auto_isf_consoleLog = mutableListOf(),
        )
        return buildJsonObject { put("rt", json.parseToJsonElement(rt.serialize())) }
    }

    private fun selftest(): String = Selftest.run(logger)

    private fun JsonElement?.toKotlinValue(): Any? = when (this) {
        null -> null
        is JsonNull -> null
        is JsonPrimitive -> when {
            isString -> content
            booleanOrNull != null -> boolean
            longOrNull != null -> long
            doubleOrNull != null -> double
            else -> content
        }
        else -> toString()
    }

    private fun JsonObject.toJson(): JsonObject = buildJsonObject {
        put("ts", long("ts"))
        put("sgv", num("sgv"))
        put("profile", str("profile") ?: "")
    }

    private fun GlucoseStatusAutoIsf.toJson(): JsonObject = buildJsonObject {
        put("glucose", glucose)
        put("noise", noise)
        put("delta", delta)
        put("shortAvgDelta", shortAvgDelta)
        put("longAvgDelta", longAvgDelta)
        put("date", date)
        put("duraISFminutes", duraISFminutes)
        put("duraISFaverage", duraISFaverage)
        put("parabolaMinutes", parabolaMinutes)
        put("deltaPl", deltaPl)
        put("deltaPn", deltaPn)
        put("bgAcceleration", bgAcceleration)
        put("a0", a0)
        put("a1", a1)
        put("a2", a2)
        put("corrSqu", corrSqu)
    }

    private fun IobTotal.toJson(): JsonObject = buildJsonObject {
        put("time", time)
        put("iob", iob)
        put("activity", activity)
        put("bolussnooze", bolussnooze)
        put("basaliob", basaliob)
        put("netbasalinsulin", netbasalinsulin)
        put("hightempinsulin", hightempinsulin)
        put("lastBolusTime", lastBolusTime)
        put("netInsulin", netInsulin)
        put("extendedBolusInsulin", extendedBolusInsulin)
        iobWithZeroTemp?.let { put("iobWithZeroTemp", it.toJson()) }
    }

    private fun JsonObject.toGlucoseStatus(): GlucoseStatusAutoIsf = GlucoseStatusAutoIsf(
        glucose = num("glucose"),
        noise = num("noise"),
        delta = num("delta"),
        shortAvgDelta = num("shortAvgDelta"),
        longAvgDelta = num("longAvgDelta"),
        date = long("date"),
        duraISFminutes = num("duraISFminutes"),
        duraISFaverage = num("duraISFaverage"),
        parabolaMinutes = num("parabolaMinutes"),
        deltaPl = num("deltaPl"),
        deltaPn = num("deltaPn"),
        bgAcceleration = num("bgAcceleration"),
        a0 = num("a0"),
        a1 = num("a1"),
        a2 = num("a2"),
        corrSqu = num("corrSqu"),
    )

    private fun JsonObject.toCurrentTemp(): CurrentTemp = CurrentTemp(
        duration = int("duration"),
        rate = num("rate"),
        minutesrunning = (this["minutesrunning"] as? JsonPrimitive)?.content?.toIntOrNull(),
    )

    private fun JsonObject.toIobTotal(): IobTotal = IobTotal(
        time = long("time"),
        iob = num("iob"),
        activity = num("activity"),
        bolussnooze = num("bolussnooze"),
        basaliob = num("basaliob"),
        netbasalinsulin = num("netbasalinsulin"),
        hightempinsulin = num("hightempinsulin"),
        lastBolusTime = long("lastBolusTime"),
        netInsulin = num("netInsulin"),
        extendedBolusInsulin = num("extendedBolusInsulin"),
        iobWithZeroTemp = obj("iobWithZeroTemp")?.toIobTotal(),
    )

    private fun JsonObject.toAutosens(): AutosensResult = AutosensResult(
        ratio = num("ratio", 1.0),
        carbsAbsorbed = num("carbsAbsorbed"),
        sensResult = str("sensResult") ?: "autosens not available",
    )

    private fun JsonObject.toMealData(): MealData = MealData(
        carbs = num("carbs"),
        mealCOB = num("mealCOB"),
        slopeFromMaxDeviation = num("slopeFromMaxDeviation"),
        slopeFromMinDeviation = num("slopeFromMinDeviation", 999.0),
        lastBolusTime = long("lastBolusTime"),
        lastCarbTime = long("lastCarbTime"),
        usedMinCarbsImpact = num("usedMinCarbsImpact"),
    )

    private fun JsonObject.toOapsProfile(): OapsProfileAutoIsf = OapsProfileAutoIsf(
        dia = d("dia", 0.0),
        min_5m_carbimpact = d("min_5m_carbimpact", 0.0),
        max_iob = d("max_iob", preferences.get(app.aaps.core.keys.DoubleKey.ApsSmbMaxIob)),
        max_daily_basal = d("max_daily_basal", 3.0),
        max_basal = d("max_basal", preferences.get(app.aaps.core.keys.DoubleKey.ApsMaxBasal)),
        min_bg = d("min_bg", 100.0),
        max_bg = d("max_bg", 100.0),
        target_bg = d("target_bg", 100.0),
        carb_ratio = d("carb_ratio", 10.0),
        variable_carb_ratio = optDouble("variable_carb_ratio"),
        sens = d("sens", 100.0),
        activity_detection = b("activity_detection", preferences.get(app.aaps.core.keys.BooleanKey.ActivityMonitorDetection)),
        recent_steps_5_minutes = optInt("recent_steps_5_minutes"),
        recent_steps_10_minutes = optInt("recent_steps_10_minutes"),
        recent_steps_15_minutes = optInt("recent_steps_15_minutes"),
        recent_steps_30_minutes = optInt("recent_steps_30_minutes"),
        recent_steps_60_minutes = optInt("recent_steps_60_minutes"),
        phone_moved = b("phone_moved", false),
        time_since_start = optLong("time_since_start"),
        now = optInt("now"),
        autosens_adjust_targets = b("autosens_adjust_targets", false),
        max_daily_safety_multiplier = d("max_daily_safety_multiplier", preferences.get(app.aaps.core.keys.DoubleKey.ApsMaxDailyMultiplier)),
        current_basal_safety_multiplier = d("current_basal_safety_multiplier", preferences.get(app.aaps.core.keys.DoubleKey.ApsMaxCurrentBasalMultiplier)),
        high_temptarget_raises_sensitivity = b("high_temptarget_raises_sensitivity", preferences.get(app.aaps.core.keys.BooleanKey.ApsAutoIsfHighTtRaisesSens)),
        low_temptarget_lowers_sensitivity = b("low_temptarget_lowers_sensitivity", preferences.get(app.aaps.core.keys.BooleanKey.ApsAutoIsfLowTtLowersSens)),
        sensitivity_raises_target = b("sensitivity_raises_target", preferences.get(app.aaps.core.keys.BooleanKey.ApsSensitivityRaisesTarget)),
        resistance_lowers_target = b("resistance_lowers_target", preferences.get(app.aaps.core.keys.BooleanKey.ApsResistanceLowersTarget)),
        adv_target_adjustments = b("adv_target_adjustments", false),
        exercise_mode = b("exercise_mode", false),
        half_basal_exercise_target = i("half_basal_exercise_target", 160),
        maxCOB = i("maxCOB", 120),
        skip_neutral_temps = b("skip_neutral_temps", false),
        remainingCarbsCap = i("remainingCarbsCap", 90),
        enableUAM = b("enableUAM", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseUam)),
        A52_risk_enable = b("A52_risk_enable", false),
        SMBInterval = i("SMBInterval", preferences.get(app.aaps.core.keys.IntKey.ApsMaxSmbFrequency)),
        enableSMB_with_COB = b("enableSMB_with_COB", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseSmbWithCob)),
        enableSMB_with_temptarget = b("enableSMB_with_temptarget", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseSmbWithLowTt)),
        allowSMB_with_high_temptarget = b("allowSMB_with_high_temptarget", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseSmbWithHighTt)),
        enableSMB_always = b("enableSMB_always", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseSmbAlways)),
        enableSMB_after_carbs = b("enableSMB_after_carbs", preferences.get(app.aaps.core.keys.BooleanKey.ApsUseSmbAfterCarbs)),
        maxSMBBasalMinutes = i("maxSMBBasalMinutes", preferences.get(app.aaps.core.keys.IntKey.ApsMaxMinutesOfBasalToLimitSmb)),
        maxUAMSMBBasalMinutes = i("maxUAMSMBBasalMinutes", preferences.get(app.aaps.core.keys.IntKey.ApsUamMaxMinutesOfBasalToLimitSmb)),
        bolus_increment = d("bolus_increment", 0.05),
        carbsReqThreshold = i("carbsReqThreshold", preferences.get(app.aaps.core.keys.IntKey.ApsCarbsRequestThreshold)),
        current_basal = d("current_basal", 0.0),
        temptargetSet = b("temptargetSet", false),
        autosens_max = d("autosens_max", preferences.get(app.aaps.core.keys.DoubleKey.AutosensMax)),
        out_units = str("out_units") ?: "mg/dl",
        lgsThreshold = optInt("lgsThreshold"),
        variable_sens = d("variable_sens", d("sens", 100.0)),
        autoISF_version = str("autoISF_version") ?: "3.2.0",
        enable_autoISF = b("enable_autoISF", true),
        autoISF_max = d("autoISF_max", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfMax)),
        autoISF_min = d("autoISF_min", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfMin)),
        bgAccel_ISF_weight = d("bgAccel_ISF_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfBgAccelWeight)),
        bgBrake_ISF_weight = d("bgBrake_ISF_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfBgBrakeWeight)),
        pp_ISF_weight = d("pp_ISF_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfPpWeight)),
        lower_ISFrange_weight = d("lower_ISFrange_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfLowBgWeight)),
        higher_ISFrange_weight = d("higher_ISFrange_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfHighBgWeight)),
        dura_ISF_weight = d("dura_ISF_weight", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfDuraWeight)),
        smb_delivery_ratio = d("smb_delivery_ratio", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfSmbDeliveryRatio)),
        smb_delivery_ratio_min = d("smb_delivery_ratio_min", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfSmbDeliveryRatioMin)),
        smb_delivery_ratio_max = d("smb_delivery_ratio_max", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfSmbDeliveryRatioMax)),
        smb_delivery_ratio_bg_range = d("smb_delivery_ratio_bg_range", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfSmbDeliveryRatioBgRange)),
        smb_max_range_extension = d("smb_max_range_extension", preferences.get(app.aaps.core.keys.DoubleKey.ApsAutoIsfSmbMaxRangeExtension)),
        enableSMB_EvenOn_OddOff_always = b("enableSMB_EvenOn_OddOff_always", preferences.get(app.aaps.core.keys.BooleanKey.ApsAutoIsfSmbOnEvenTarget)),
        iob_threshold_percent = i("iob_threshold_percent", preferences.get(app.aaps.core.keys.IntKey.ApsAutoIsfIobThPercent)),
        profile_percentage = i("profile_percentage", 100),
    )

    private fun JsonObject.d(name: String, fallback: Double): Double = if (containsKey(name)) num(name) else fallback
    private fun JsonObject.i(name: String, fallback: Int): Int = if (containsKey(name)) int(name) else fallback
    private fun JsonObject.b(name: String, fallback: Boolean): Boolean = if (containsKey(name)) bool(name) else fallback

    private fun JsonObject.optDouble(name: String): Double? = (this[name] as? JsonPrimitive)?.content?.toDoubleOrNull()
    private fun JsonObject.optInt(name: String): Int? = (this[name] as? JsonPrimitive)?.content?.toIntOrNull()
    private fun JsonObject.optLong(name: String): Long? = (this[name] as? JsonPrimitive)?.content?.toLongOrNull()

    companion object {

        @JvmStatic
        fun main(args: Array<String>) {
            val bridge = Bridge()
            if (args.firstOrNull() == "selftest") {
                println(bridge.selftest())
                return
            }
            System.`in`.bufferedReader().forEachLine { line ->
                if (line.isBlank()) return@forEachLine
                val response = try {
                    bridge.handle(line)
                } catch (e: Throwable) {
                    buildJsonObject {
                        put("error", e::class.simpleName + ": " + (e.message ?: ""))
                        put("cmd", json.parseToJsonElement(line).jsonObject.str("cmd") ?: "")
                    }
                }
                println(response.toString())
                System.out.flush()
            }
        }
    }
}

fun main(args: Array<String>) = Bridge.main(args)

object Selftest {

    fun run(logger: ConsoleLogger): String {
        val clock = SimClock()
        val currentTime = 1656358822000L
        clock.now = currentTime
        val preferences = SimPreferences().asPreferences()
        val sut = DetermineBasalAutoISF(profileUtilProxy())
        sut.preferences = preferences

        val glucose = app.aaps.core.interfaces.aps.GlucoseStatusSMB(
            glucose = 150.0,
            noise = 0.0,
            delta = 5.0,
            shortAvgDelta = 5.0,
            longAvgDelta = 5.0,
            date = currentTime,
        )
        val iob: Array<IobTotal> = Array(48) { i ->
            val time = currentTime + i * 5 * 60000L
            IobTotal(
                time = time,
                iob = 1.0,
                activity = 0.0,
                lastBolusTime = currentTime - 3600000L,
                iobWithZeroTemp = IobTotal(time = time, iob = 1.0, activity = 0.0),
            )
        }
        val profile = OapsProfileAutoIsf(
            dia = 0.0, min_5m_carbimpact = 0.0, max_iob = 7.0, max_daily_basal = 1.0, max_basal = 4.0,
            min_bg = 100.0, max_bg = 100.0, target_bg = 100.0, carb_ratio = 10.0, sens = 50.0,
            autosens_adjust_targets = false, max_daily_safety_multiplier = 3.0, current_basal_safety_multiplier = 4.0,
            high_temptarget_raises_sensitivity = false, low_temptarget_lowers_sensitivity = false,
            sensitivity_raises_target = false, resistance_lowers_target = false, adv_target_adjustments = false,
            exercise_mode = false, half_basal_exercise_target = 160, maxCOB = 120, skip_neutral_temps = false,
            remainingCarbsCap = 90, enableUAM = true, A52_risk_enable = false, SMBInterval = 3,
            enableSMB_with_COB = false, enableSMB_with_temptarget = false, allowSMB_with_high_temptarget = false,
            enableSMB_always = false, enableSMB_after_carbs = false, maxSMBBasalMinutes = 30,
            maxUAMSMBBasalMinutes = 30, bolus_increment = 0.1, carbsReqThreshold = 1, current_basal = 1.0,
            temptargetSet = false, autosens_max = 1.2, out_units = "mg/dl", lgsThreshold = null,
            variable_sens = 50.0, autoISF_version = "3.1", enable_autoISF = false, autoISF_max = 1.0,
            autoISF_min = 1.0, bgAccel_ISF_weight = 0.0, bgBrake_ISF_weight = 0.0, pp_ISF_weight = 0.0,
            lower_ISFrange_weight = 0.0, higher_ISFrange_weight = 0.0, dura_ISF_weight = 0.0,
            smb_delivery_ratio = 0.5, smb_delivery_ratio_min = 0.5, smb_delivery_ratio_max = 0.5,
            smb_delivery_ratio_bg_range = 0.0, smb_max_range_extension = 1.0,
            enableSMB_EvenOn_OddOff_always = false, iob_threshold_percent = 100, profile_percentage = 100,
        )
        val rt = sut.determine_basal(
            glucose_status = glucose,
            currenttemp = CurrentTemp(0, 0.0, null),
            iob_data_array = iob,
            profile = profile,
            autosens_data = AutosensResult(ratio = 1.0),
            meal_data = MealData(),
            microBolusAllowed = false,
            currentTime = currentTime,
            flatBGsDetected = false,
            autoIsfMode = false,
            loop_wanted_smb = "AAPS",
            profile_percentage = 100,
            smb_ratio = 0.5,
            smb_max_range_extension = 1.0,
            iob_threshold_percent = 100,
            activity_consoleLog = "",
            auto_isf_consoleError = mutableListOf(),
            auto_isf_consoleLog = mutableListOf(),
        )
        return rt.serialize()
    }
}
