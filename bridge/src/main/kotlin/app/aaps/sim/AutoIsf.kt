package app.aaps.sim

import app.aaps.core.data.model.TT
import app.aaps.core.interfaces.aps.APSResult
import app.aaps.core.interfaces.constraints.Constraint
import app.aaps.core.interfaces.constraints.ConstraintsChecker
import app.aaps.core.interfaces.profile.Profile
import app.aaps.core.interfaces.utils.DateUtil
import app.aaps.core.interfaces.utils.HardLimits
import app.aaps.core.keys.interfaces.Preferences
import app.aaps.core.objects.constraints.ConstraintObject
import app.aaps.plugins.aps.dynamiccr.DynamicCarbRatioCalculator
import app.aaps.plugins.aps.openAPSAutoISF.DetermineBasalAutoISF
import app.aaps.plugins.aps.openAPSAutoISF.OpenAPSAutoISFPlugin
import kotlinx.coroutines.runBlocking
import java.lang.reflect.Proxy

class AutoIsfRunner(
    private val preferences: Preferences,
    private val dateUtil: DateUtil,
    private val clock: SimClock,
    private val logger: ConsoleLogger,
    private val pipeline: GlucosePipeline,
) {

    @Volatile
    var activeTempTarget: TT? = null

    private val constraintsChecker: ConstraintsChecker =
        Proxy.newProxyInstance(ConstraintsChecker::class.java.classLoader, arrayOf(ConstraintsChecker::class.java)) { _, method, args ->
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

    private val persistenceLayer: app.aaps.core.interfaces.db.PersistenceLayer =
        Proxy.newProxyInstance(app.aaps.core.interfaces.db.PersistenceLayer::class.java.classLoader, arrayOf(app.aaps.core.interfaces.db.PersistenceLayer::class.java)) { _, method, args ->
            when (method.name) {
                "getTemporaryTargetActiveAt" -> activeTempTarget
                "toString" -> "PersistenceLayerStub"
                else -> defaultValue(method.returnType)
            }
        } as app.aaps.core.interfaces.db.PersistenceLayer

    private val plugin: OpenAPSAutoISFPlugin = OpenAPSAutoISFPlugin(
        aapsLogger = logger,
        rxBus = anyProxy(app.aaps.core.interfaces.rx.bus.RxBus::class.java) as app.aaps.core.interfaces.rx.bus.RxBus,
        constraintsChecker = constraintsChecker,
        rh = textResolverProxy(),
        profileFunction = anyProxy(app.aaps.core.interfaces.profile.ProfileFunction::class.java) as app.aaps.core.interfaces.profile.ProfileFunction,
        profileUtil = profileUtilProxy(),
        config = anyProxy(app.aaps.core.interfaces.configuration.Config::class.java) as app.aaps.core.interfaces.configuration.Config,
        activePlugin = anyProxy(app.aaps.core.interfaces.plugin.ActivePlugin::class.java) as app.aaps.core.interfaces.plugin.ActivePlugin,
        iobCobCalculator = pipeline.iobCobCalculator,
        hardLimits = hardLimits,
        preferences = preferences,
        dateUtil = dateUtil,
        processedTbrEbData = anyProxy(app.aaps.core.interfaces.db.ProcessedTbrEbData::class.java) as app.aaps.core.interfaces.db.ProcessedTbrEbData,
        persistenceLayer = persistenceLayer,
        glucoseStatusProvider = anyProxy(app.aaps.core.interfaces.iob.GlucoseStatusProvider::class.java) as app.aaps.core.interfaces.iob.GlucoseStatusProvider,
        bgQualityCheck = anyProxy(app.aaps.core.interfaces.bgQualityCheck.BgQualityCheck::class.java) as app.aaps.core.interfaces.bgQualityCheck.BgQualityCheck,
        notificationManager = anyProxy(app.aaps.core.interfaces.notifications.NotificationManager::class.java) as app.aaps.core.interfaces.notifications.NotificationManager,
        determineBasalAutoISF = DetermineBasalAutoISF(profileUtilProxy()).also { it.preferences = preferences },
        profiler = anyProxy(app.aaps.core.interfaces.profiling.Profiler::class.java) as app.aaps.core.interfaces.profiling.Profiler,
        glucoseStatusCalculatorAutoIsf = pipeline.calculator,
        apsResultProvider = { anyProxy(APSResult::class.java) as APSResult },
        ch = anyProxy(app.aaps.core.interfaces.insulin.ConcentrationHelper::class.java) as app.aaps.core.interfaces.insulin.ConcentrationHelper,
        dynamicCarbRatioCalculator = DynamicCarbRatioCalculator(),
        tddCalculator = anyProxy(app.aaps.core.interfaces.stats.TddCalculator::class.java) as app.aaps.core.interfaces.stats.TddCalculator,
        insulin = anyProxy(app.aaps.core.interfaces.insulin.Insulin::class.java) as app.aaps.core.interfaces.insulin.Insulin,
    )

    fun autoIsf(now: Long, profile: Profile, tempTarget: TT?): Double {
        clock.now = now
        activeTempTarget = tempTarget
        return runBlocking { plugin.autoISF(profile) }
    }

    @Suppress("UNCHECKED_CAST")
    fun console(): List<String> {
        val field = OpenAPSAutoISFPlugin::class.java.getDeclaredField("consoleError")
        field.isAccessible = true
        return (field.get(plugin) as MutableList<String>).toList()
    }
}
