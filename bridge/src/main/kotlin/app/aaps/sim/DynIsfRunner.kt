package app.aaps.sim

import androidx.collection.LongSparseArray
import app.aaps.core.data.aps.AverageTDD
import app.aaps.core.data.model.TDD
import app.aaps.core.interfaces.aps.APSResult
import app.aaps.core.interfaces.constraints.Constraint
import app.aaps.core.interfaces.constraints.ConstraintsChecker
import app.aaps.core.interfaces.db.PersistenceLayer
import app.aaps.core.interfaces.db.ProcessedTbrEbData
import app.aaps.core.interfaces.iob.GlucoseStatusProvider
import app.aaps.core.interfaces.profile.Profile
import app.aaps.core.interfaces.profile.ProfileFunction
import app.aaps.core.interfaces.stats.TddCalculator
import app.aaps.core.interfaces.utils.DateUtil
import app.aaps.core.interfaces.utils.HardLimits
import app.aaps.core.interfaces.utils.fabric.FabricPrivacy
import app.aaps.core.keys.interfaces.Preferences
import app.aaps.core.objects.constraints.ConstraintObject
import app.aaps.core.objects.profile.ProfileSealed
import app.aaps.plugins.aps.dynamiccr.DynamicCarbRatioCalculator
import app.aaps.plugins.aps.openAPS.DeltaCalculator
import app.aaps.plugins.aps.openAPSSMB.DetermineBasalSMB
import app.aaps.plugins.aps.openAPSSMB.GlucoseStatusCalculatorSMB
import app.aaps.plugins.aps.openAPSSMB.OpenAPSSMBPlugin
import kotlinx.coroutines.runBlocking
import java.lang.reflect.Proxy

/**
 * Wraps the real OpenAPSSMBPlugin only to call getIsfMgdl(), i.e. the real DynISF formula.
 * No formula is reimplemented here; TDD is taken from the profile (dynisf_use_profile_sens).
 */
class DynIsfRunner(
    private val preferences: Preferences,
    private val dateUtil: DateUtil,
    private val clock: SimClock,
    private val logger: ConsoleLogger,
    private val pipeline: GlucosePipeline,
    private val tdd: Double,
) {

    @Volatile
    private var currentProfile: Profile? = null

    @Volatile
    var lastStatusGlucose: Double? = null

    private val constraintsChecker: ConstraintsChecker =
        Proxy.newProxyInstance(ConstraintsChecker::class.java.classLoader, arrayOf(ConstraintsChecker::class.java)) { _, method, _ ->
            when (method.name) {
                "isAutosensModeEnabled" -> ConstraintObject(false, logger)
                "isUAMEnabled" -> ConstraintObject(true, logger)
                "isSMBModeEnabled" -> ConstraintObject(true, logger)
                "getMaxIOBAllowed" -> ConstraintObject(12.0, logger)
                "getMaxBasalAllowed" -> ConstraintObject(4.0, logger)
                "toString" -> "ConstraintsCheckerStub"
                else -> defaultValue(method.returnType)
            }
        } as ConstraintsChecker

    private val hardLimits: HardLimits =
        Proxy.newProxyInstance(HardLimits::class.java.classLoader, arrayOf(HardLimits::class.java)) { _, method, args ->
            when {
                method.name.startsWith("verifyHardLimits") -> {
                    if (method.returnType == java.lang.Integer.TYPE) (args?.getOrNull(0) as? Number)?.toInt() ?: 0
                    else (args?.getOrNull(0) as? Number)?.toDouble() ?: 0.0
                }
                method.name == "toString" -> "HardLimitsStub"
                else -> defaultValue(method.returnType)
            }
        } as HardLimits

    private val persistenceLayer: PersistenceLayer =
        Proxy.newProxyInstance(PersistenceLayer::class.java.classLoader, arrayOf(PersistenceLayer::class.java)) { _, method, _ ->
            when (method.name) {
                "getApsResultCloseTo", "getTemporaryTargetActiveAt" -> null
                "toString" -> "PersistenceLayerStub"
                else -> defaultValue(method.returnType)
            }
        } as PersistenceLayer

    private val profileFunction: ProfileFunction =
        Proxy.newProxyInstance(ProfileFunction::class.java.classLoader, arrayOf(ProfileFunction::class.java)) { _, method, _ ->
            when (method.name) {
                "getProfile" -> currentProfile
                "toString" -> "ProfileFunctionStub"
                else -> defaultValue(method.returnType)
            }
        } as ProfileFunction

    private val tddCalculator: TddCalculator = object : TddCalculator {
        private fun value() = TDD(timestamp = dateUtil.now(), totalAmount = tdd)
        private fun array() = LongSparseArray<TDD>().apply { put(dateUtil.now(), value()) }
        override suspend fun calculate(timestamp: Long, days: Long, allowMissingDays: Boolean) = array()
        override suspend fun calculate(days: Long, allowMissingDays: Boolean) = array()
        override suspend fun calculateToday() = value()
        override suspend fun calculateDaily(startHours: Long, endHours: Long) = value()
        override suspend fun calculateDaily(timestamp: Long, startHours: Long, endHours: Long) = value()
        override suspend fun calculateInterval(startTime: Long, endTime: Long, allowMissingData: Boolean) = value()
        override suspend fun calculateIntervalWithCachedDays(startTime: Long, endTime: Long, allowMissingData: Boolean) = value()
        override fun averageTDD(tdds: LongSparseArray<TDD>?) = AverageTDD(value(), true)
    }

    private val smbCalculator = GlucoseStatusCalculatorSMB(
        logger,
        pipeline.iobCobCalculator,
        dateUtil,
        ConsoleDecimalFormatter(),
        DeltaCalculator(logger),
    )

    private val plugin: OpenAPSSMBPlugin = OpenAPSSMBPlugin(
        aapsLogger = logger,
        rxBus = anyProxy(app.aaps.core.interfaces.rx.bus.RxBus::class.java) as app.aaps.core.interfaces.rx.bus.RxBus,
        constraintsChecker = constraintsChecker,
        rh = textResolverProxy(),
        profileFunction = profileFunction,
        profileUtil = profileUtilProxy(),
        config = anyProxy(app.aaps.core.interfaces.configuration.Config::class.java) as app.aaps.core.interfaces.configuration.Config,
        activePlugin = anyProxy(app.aaps.core.interfaces.plugin.ActivePlugin::class.java) as app.aaps.core.interfaces.plugin.ActivePlugin,
        iobCobCalculator = pipeline.iobCobCalculator,
        hardLimits = hardLimits,
        preferences = preferences,
        dateUtil = dateUtil,
        processedTbrEbData = anyProxy(ProcessedTbrEbData::class.java) as ProcessedTbrEbData,
        persistenceLayer = persistenceLayer,
        glucoseStatusProvider = smbCalculator,
        tddCalculator = tddCalculator,
        bgQualityCheck = anyProxy(app.aaps.core.interfaces.bgQualityCheck.BgQualityCheck::class.java) as app.aaps.core.interfaces.bgQualityCheck.BgQualityCheck,
        notificationManager = anyProxy(app.aaps.core.interfaces.notifications.NotificationManager::class.java) as app.aaps.core.interfaces.notifications.NotificationManager,
        dynamicCarbRatioCalculator = DynamicCarbRatioCalculator(),
        determineBasalSMB = DetermineBasalSMB(profileUtilProxy(), anyProxy(FabricPrivacy::class.java) as FabricPrivacy),
        profiler = anyProxy(app.aaps.core.interfaces.profiling.Profiler::class.java) as app.aaps.core.interfaces.profiling.Profiler,
        glucoseStatusCalculatorSMB = smbCalculator,
        apsResultProvider = { anyProxy(APSResult::class.java) as APSResult },
        ch = anyProxy(app.aaps.core.interfaces.insulin.ConcentrationHelper::class.java) as app.aaps.core.interfaces.insulin.ConcentrationHelper,
        fabricPrivacy = anyProxy(FabricPrivacy::class.java) as FabricPrivacy,
    )

    fun sens(now: Long, profile: ProfileSealed.EPS): Double? {
        clock.now = now
        currentProfile = profile
        lastStatusGlucose = smbCalculator.getGlucoseStatusData(false)?.glucose
        return runBlocking { plugin.getIsfMgdl(profile, "bench") }
    }
}
