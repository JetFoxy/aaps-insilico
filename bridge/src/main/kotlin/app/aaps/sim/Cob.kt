package app.aaps.sim

import app.aaps.core.data.aps.BasalData
import app.aaps.core.data.iob.CobInfo
import app.aaps.core.data.model.CA
import app.aaps.core.data.model.GlucoseUnit
import app.aaps.core.data.model.TE
import app.aaps.core.interfaces.aps.AutosensData
import app.aaps.core.interfaces.aps.AutosensDataStore
import app.aaps.core.interfaces.aps.AutosensResult
import app.aaps.core.interfaces.aps.IobTotal
import app.aaps.core.interfaces.aps.MealData
import app.aaps.core.interfaces.configuration.Config
import app.aaps.core.interfaces.db.PersistenceLayer
import app.aaps.core.interfaces.db.ProcessedTbrEbData
import app.aaps.core.interfaces.iob.IobCobCalculator
import app.aaps.core.interfaces.logging.AAPSLogger
import app.aaps.core.interfaces.nsclient.ProcessedDeviceStatusData
import app.aaps.core.interfaces.overview.OverviewData
import app.aaps.core.interfaces.overview.graph.OverviewDataCache
import app.aaps.core.interfaces.plugin.ActivePlugin
import app.aaps.core.interfaces.profile.EffectiveProfile
import app.aaps.core.interfaces.profile.Profile
import app.aaps.core.interfaces.profile.ProfileFunction
import app.aaps.core.interfaces.profile.ProfileUtil
import app.aaps.core.interfaces.profiling.Profiler
import app.aaps.core.interfaces.rx.bus.RxBus
import app.aaps.core.interfaces.utils.DateUtil
import app.aaps.core.interfaces.utils.DecimalFormatter
import app.aaps.core.interfaces.workflow.CalculationSignalsEmitter
import app.aaps.core.interfaces.workflow.CalculationWorkflow
import app.aaps.core.keys.interfaces.Preferences
import app.aaps.implementation.iob.AutosensDataObject
import app.aaps.workflow.PrepareGraphData
import app.aaps.workflow.PrepareGraphDataRunner
import app.aaps.workflow.WorkflowChainData
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import java.lang.reflect.Proxy
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

class SimIobCobCalculator(
    override var ads: AutosensDataStore,
    private val iobBuilder: IobBuilder,
    private val boluses: () -> List<BolusRecord>,
    private val temps: () -> List<TempRecord>,
    private val mealAt: (Long) -> MealData,
    private val profileFor: (Long) -> EffectiveProfile,
) : IobCobCalculator {

    override suspend fun getMealDataWithWaitingForCalculationFinish(): MealData = mealAt(System.currentTimeMillis())

    override suspend fun getLastAutosensDataWithWaitForCalculationFinish(reason: String): AutosensData? {
        val table = ads.autosensDataTable
        if (table.size() == 0) return null
        return table.valueAt(table.size() - 1)
    }

    override suspend fun calculateFromTreatmentsAndTemps(toTime: Long, profile: EffectiveProfile): IobTotal =
        iobBuilder.totalAt(toTime, profileFor, boluses(), temps())

    override suspend fun calculateDetectionStart(from: Long, limitDataToOldestAvailable: Boolean): Long =
        from - 32L * 60 * 60 * 1000

    override suspend fun getBasalData(profile: Profile, fromTime: Long): BasalData = BasalData()

    override suspend fun calculateIobArrayInDia(profile: EffectiveProfile): Array<IobTotal> = emptyArray()

    override suspend fun calculateIobArrayForSMB(
        lastAutosensResult: AutosensResult,
        exerciseMode: Boolean,
        halfBasalExerciseTarget: Int,
        isTempTarget: Boolean,
    ): Array<IobTotal> = emptyArray()

    override fun iobArrayToString(array: Array<IobTotal>): String = array.joinToString()

    override fun clearCache() = Unit
    override fun bgDataReloaded() = Unit
    override suspend fun getCobInfo(reason: String): CobInfo = CobInfo(0L, 0.0, 0.0)
    override suspend fun calculateIobFromBolus(): IobTotal = IobTotal(0)
    override suspend fun calculateAbsoluteIobFromBaseBasals(toTime: Long): IobTotal = IobTotal(toTime)
    override suspend fun calculateIobToTimeFromTempBasalsIncludingConvertedExtended(toTime: Long): IobTotal = IobTotal(toTime)
    override suspend fun calculateIobFromTempBasalsIncludingConvertedExtended(): IobTotal = IobTotal(0)
}

class CobRunner(
    private val preferences: Preferences,
    private val dateUtil: DateUtil,
    private val logger: AAPSLogger,
    private val calculator: SimIobCobCalculator,
    private val profile: EffectiveProfile,
    private val carbs: List<CA>,
) {

    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())

    fun mealData(now: Long): MealData {
        val data = run(now)
        val meal = MealData()
        meal.carbs = carbs.filter { it.timestamp in (now - 6L * 60 * 60 * 1000)..now }.sumOf { it.amount }
        if (data != null) {
            meal.mealCOB = data.cob
            meal.slopeFromMaxDeviation = data.slopeFromMaxDeviation
            meal.slopeFromMinDeviation = data.slopeFromMinDeviation
            meal.usedMinCarbsImpact = data.usedMinCarbsImpact
            meal.lastCarbTime = carbs.lastOrNull { it.timestamp <= now }?.timestamp ?: 0L
        }
        return meal
    }

    fun autosensData(now: Long): AutosensData? = run(now)

    private fun run(now: Long): AutosensData? {
        val runner = PrepareGraphDataRunner(
            aapsLogger = logger,
            workflowChainData = WorkflowChainData(logger),
            dateUtil = dateUtil,
            rxBus = proxy(RxBus::class.java) { _, _ -> Unit },
            persistenceLayer = persistenceLayer(now),
            activePlugin = activePlugin(),
            profileFunction = profileFunction(),
            profileUtil = profileUtilProxy(),
            preferences = preferences,
            config = proxy(Config::class.java) { method, _ -> if (method.name == "getAAPSCLIENT") false else defaultValue(method.returnType) },
            profiler = proxy(Profiler::class.java) { _, _ -> Unit },
            decimalFormatter = ConsoleDecimalFormatter(),
            processedDeviceStatusData = proxy(ProcessedDeviceStatusData::class.java) { _, _ -> null },
            scope = scope,
            autosensDataProvider = { AutosensDataObject(logger, preferences, dateUtil) },
        )
        val prepare = PrepareGraphData(
            iobCobCalculator = calculator,
            overviewData = overviewData(now),
            cache = proxy(OverviewDataCache::class.java) { method, _ -> defaultValue(method.returnType) },
            signals = proxy(CalculationSignalsEmitter::class.java) { _, _ -> Unit },
            reason = "sim",
            end = now,
            bgDataReload = false,
            limitDataToOldestAvailable = false,
            triggeredByNewBG = false,
            emitFinalProgress = false,
        )
        return runBlocking {
            try {
                invokeAutosens(runner, prepare)
            } catch (e: Throwable) {
                logger.error("CobRunner failed: ${e.message}", e)
                return@runBlocking null
            }
            val table = calculator.ads.autosensDataTable
            if (table.size() == 0) null else table.valueAt(table.size() - 1)
        }
    }

    private suspend fun invokeAutosens(runner: PrepareGraphDataRunner, data: PrepareGraphData) {
        val method = PrepareGraphDataRunner::class.java.getDeclaredMethod(
            "runIobCobOref1",
            PrepareGraphData::class.java,
            Function0::class.java,
            Function0::class.java,
            Continuation::class.java,
        )
        method.isAccessible = true
        suspendCoroutine<Unit> { continuation ->
            val result = method.invoke(runner, data, { false }, { false }, continuation)
            if (result !== kotlin.coroutines.intrinsics.COROUTINE_SUSPENDED) continuation.resume(Unit)
        }
    }

    private fun persistenceLayer(now: Long): PersistenceLayer =
        proxy(PersistenceLayer::class.java) { method, args ->
            when (method.name) {
                "getCarbsFromTimeToTimeExpanded" -> {
                    val from = args?.getOrNull(0) as? Long ?: 0L
                    val to = args?.getOrNull(1) as? Long ?: 0L
                    carbs.filter { it.timestamp in from..to }
                }
                "getTherapyEventDataFromTime", "getProfileSwitchesFromTime" -> emptyList<Any>()
                "getBgReadingsDataFromTimeToTime" -> emptyList<Any>()
                "getTemporaryTargetActiveAt" -> null
                "toString" -> "PersistenceLayerStub"
                else -> defaultValue(method.returnType)
            }
        }

    private fun profileFunction(): ProfileFunction =
        proxy(ProfileFunction::class.java) { method, args ->
            when (method.name) {
                "getProfile" -> profile
                "getUnits" -> GlucoseUnit.MGDL
                "isProfileValid" -> true
                "toString" -> "ProfileFunctionStub"
                else -> defaultValue(method.returnType)
            }
        }

    private fun activePlugin(): ActivePlugin {
        val sensitivity = proxy<app.aaps.core.interfaces.aps.Sensitivity>(app.aaps.core.interfaces.aps.Sensitivity::class.java) { method, _ ->
            when (method.name) {
                "isOref1" -> true
                "isMinCarbsAbsorptionDynamic" -> false
                "maxAbsorptionHours" -> 6.0
                "detectSensitivity" -> AutosensResult(ratio = 1.0)
                "toString" -> "SensitivityStub"
                else -> defaultValue(method.returnType)
            }
        }
        return proxy<ActivePlugin>(ActivePlugin::class.java) { method, _ ->
            when (method.name) {
                "getActiveSensitivity" -> sensitivity
                "toString" -> "ActivePluginStub"
                else -> defaultValue(method.returnType)
            }
        }
    }

    private fun overviewData(now: Long): OverviewData =
        proxy(OverviewData::class.java) { method, _ ->
            when (method.name) {
                "getToTime" -> now
                "getFromTime" -> now - 6L * 60 * 60 * 1000
                "getEndTime" -> now
                "toString" -> "OverviewDataStub"
                else -> defaultValue(method.returnType)
            }
        }

    private fun <T> proxy(type: Class<T>, handler: (java.lang.reflect.Method, Array<out Any?>?) -> Any?): T {
        @Suppress("UNCHECKED_CAST")
        return Proxy.newProxyInstance(type.classLoader, arrayOf(type)) { _, method, args -> handler(method, args) } as T
    }
}
