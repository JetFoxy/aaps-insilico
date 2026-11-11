package app.aaps.sim

import app.aaps.core.data.iob.InMemoryGlucoseValue
import app.aaps.core.data.model.GV
import app.aaps.core.data.model.SourceSensor
import app.aaps.core.data.model.TrendArrow
import app.aaps.core.interfaces.aps.AutosensDataStore
import app.aaps.core.interfaces.aps.GlucoseStatusAutoIsf
import app.aaps.core.interfaces.iob.IobCobCalculator
import app.aaps.core.interfaces.utils.DateUtil
import app.aaps.core.keys.interfaces.Preferences
import app.aaps.core.objects.extensions.fromGv
import app.aaps.plugins.aps.openAPS.DeltaCalculator
import app.aaps.plugins.aps.openAPSAutoISF.GlucoseStatusCalculatorAutoIsf
import app.aaps.plugins.main.iob.iobCobCalculator.data.AutosensDataStoreObject
import app.aaps.plugins.smoothing.UnscentedKalmanFilterPlugin
import java.lang.reflect.Proxy

class GlucosePipeline(
    private val preferences: Preferences,
    private val dateUtil: DateUtil,
    private val clock: SimClock,
    private val logger: ConsoleLogger,
) {

    val store: AutosensDataStoreObject = AutosensDataStoreObject()

    private val smoothing = UnscentedKalmanFilterPlugin(
        logger,
        textResolverProxy(),
        preferences,
        anyProxy(app.aaps.core.interfaces.db.PersistenceLayer::class.java) as app.aaps.core.interfaces.db.PersistenceLayer,
        anyProxy(app.aaps.core.interfaces.notifications.NotificationManager::class.java) as app.aaps.core.interfaces.notifications.NotificationManager,
    )

    val iobCobCalculator: IobCobCalculator =
        Proxy.newProxyInstance(IobCobCalculator::class.java.classLoader, arrayOf(IobCobCalculator::class.java)) { _, method, args ->
            when (method.name) {
                "getAds" -> store
                "setAds" -> {
                    Unit
                }
                "toString" -> "IobCobCalculatorStub"
                else -> defaultValue(method.returnType)
            }
        } as IobCobCalculator

    val calculator = GlucoseStatusCalculatorAutoIsf(
        logger,
        iobCobCalculator,
        dateUtil,
        ConsoleDecimalFormatter(),
        DeltaCalculator(logger),
        preferences,
    )

    fun glucose(now: Long, readings: List<Pair<Long, Double>>, sourceSensor: SourceSensor): GlucoseStatusAutoIsf? {
        clock.now = now
        val from = now - 34L * 60 * 60 * 1000
        val to = now + 2L * 60 * 1000
        val gvs = readings.asSequence()
            .filter { it.first in from..to }
            .sortedByDescending { it.first }
            .map { (ts, value) ->
                GV(
                    timestamp = ts,
                    raw = value,
                    value = value,
                    trendArrow = TrendArrow.NONE,
                    noise = null,
                    sourceSensor = sourceSensor,
                )
            }
            .toList()
        if (gvs.size < 3) return null
        store.bgReadings = gvs
        store.createBucketedData(logger, dateUtil)

        val bucketed = store.bucketedData?.map { it.copy(smoothed = null, calibrated = null) }?.toMutableList()
        if (bucketed != null) store.bucketedData = smoothing.smooth(bucketed)
        val status = store.statusData?.map { it.copy(smoothed = null, calibrated = null) }?.toMutableList()
        if (status != null) store.statusData = smoothing.smoothWithoutLearning(status)

        return calculator.getGlucoseStatusData(allowOldData = true)
    }

    fun resetLearning() {
        val method = smoothing.javaClass.getDeclaredMethod("resetLearning")
        method.isAccessible = true
        method.invoke(smoothing)
    }

    fun bucketed(): MutableList<InMemoryGlucoseValue>? = store.bucketedData
}
