package app.aaps.sim

import app.aaps.core.data.model.GlucoseUnit
import app.aaps.core.interfaces.logging.AAPSLogger
import app.aaps.core.interfaces.logging.LTag
import app.aaps.core.interfaces.resources.TextResolver
import app.aaps.core.interfaces.utils.DateUtil
import app.aaps.core.interfaces.utils.DecimalFormatter
import app.aaps.core.keys.interfaces.NonPreferenceKey
import app.aaps.core.keys.interfaces.PreferenceItem
import app.aaps.core.keys.interfaces.Preferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.lang.reflect.InvocationHandler
import java.lang.reflect.Method
import java.lang.reflect.Proxy
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

class ConsoleLogger(private val verbose: Boolean = false) : AAPSLogger {

    private fun emit(level: String, tag: LTag?, text: String) {
        if (verbose) System.err.println("[$level${tag?.let { "/$it" } ?: ""}] $text")
    }

    override fun debug(message: String) = emit("D", null, message)
    override fun debug(enable: Boolean, tag: LTag, message: String) = emit("D", tag, message)
    override fun debug(tag: LTag, message: String) = emit("D", tag, message)
    override fun debug(tag: LTag, accessor: () -> String) = emit("D", tag, accessor())
    override fun debug(tag: LTag, format: String, vararg arguments: Any?) = emit("D", tag, format.format(*arguments))
    override fun warn(tag: LTag, message: String) = emit("W", tag, message)
    override fun warn(tag: LTag, format: String, vararg arguments: Any?) = emit("W", tag, format.format(*arguments))
    override fun info(tag: LTag, message: String) = emit("I", tag, message)
    override fun info(tag: LTag, format: String, vararg arguments: Any?) = emit("I", tag, format.format(*arguments))
    override fun error(tag: LTag, message: String) = emit("E", tag, message)
    override fun error(tag: LTag, message: String, throwable: Throwable) = emit("E", tag, "$message: $throwable")
    override fun error(tag: LTag, format: String, vararg arguments: Any?) = emit("E", tag, format.format(*arguments))
    override fun error(message: String) = emit("E", null, message)
    override fun error(message: String, throwable: Throwable) = emit("E", null, "$message: $throwable")
    override fun error(format: String, vararg arguments: Any?) = emit("E", null, format.format(*arguments))
    override fun debug(className: String, methodName: String, lineNumber: Int, tag: LTag, message: String) = emit("D", tag, message)
    override fun info(className: String, methodName: String, lineNumber: Int, tag: LTag, message: String) = emit("I", tag, message)
    override fun warn(className: String, methodName: String, lineNumber: Int, tag: LTag, message: String) = emit("W", tag, message)
    override fun error(className: String, methodName: String, lineNumber: Int, tag: LTag, message: String) = emit("E", tag, message)
}

class ConsoleDecimalFormatter : DecimalFormatter {

    private fun fmt(value: Double, digits: Int): String = String.format(Locale.US, "%.${digits}f", value)

    override fun to0Decimal(value: Double): String = fmt(value, 0)
    override fun to0Decimal(value: Double, unit: String): String = fmt(value, 0) + unit
    override fun to1Decimal(value: Double): String = fmt(value, 1)
    override fun to1Decimal(value: Double, unit: String): String = fmt(value, 1) + unit
    override fun to2Decimal(value: Double): String = fmt(value, 2)
    override fun to2Decimal(value: Double, unit: String): String = fmt(value, 2) + unit
    override fun to3Decimal(value: Double): String = fmt(value, 3)
    override fun to3Decimal(value: Double, unit: String): String = fmt(value, 3) + unit
    override fun toPumpSupportedBolus(value: Double, bolusStep: Double): String = fmt(value, 2)
    override fun toPumpSupportedBolusWithUnits(value: Double, bolusStep: Double): String = fmt(value, 2)
    override fun toPumpSupportedBolusWithUnits(value: app.aaps.core.interfaces.pump.PumpInsulin, bolusStep: Double): String = fmt(value.cU, 2)
    override fun pumpSupportedBolusFormat(bolusStep: Double): app.aaps.core.data.format.NumberFormat =
        throw UnsupportedOperationException("pumpSupportedBolusFormat is not used by the bridge")
}

class SimClock {

    var now: Long = 0L

    fun iso(ms: Long): String {
        val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date(ms))
    }

    fun dateTime(ms: Long): String {
        val format = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date(ms))
    }

    fun dateTimeSeconds(ms: Long): String {
        val format = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date(ms))
    }
}

fun dateUtilProxy(clock: SimClock): DateUtil = proxy(DateUtil::class.java) { method, args ->
    when (method.name) {
        "now" -> clock.now
        "nowWithoutMilliseconds" -> clock.now / 1000 * 1000
        "toISOString", "toISOAsUTC" -> clock.iso(args?.get(0) as Long)
        "toISONoZone" -> clock.dateTimeSeconds(args?.get(0) as Long)
        "dateAndTimeString", "dateString", "dateStringShort" -> clock.dateTime(args?.get(0) as Long)
        "dateAndTimeAndSecondsString" -> clock.dateTimeSeconds(args?.get(0) as Long)
        "timeString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(11, 16)
        "secondString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(17, 19)
        "minuteString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(14, 16)
        "hourString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(11, 13)
        "amPm" -> ""
        "dayNameString" -> ""
        "dayString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(8, 10)
        "monthString" -> clock.dateTime(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(5, 7)
        "weekString" -> ""
        "timeStringWithSeconds" -> clock.dateTimeSeconds(if (args != null && args.isNotEmpty()) args[0] as Long else clock.now).substring(11)
        "dateAndTimeRangeString" -> "${clock.dateTime(args?.get(0) as Long)} - ${clock.dateTime(args?.get(1) as Long)}"
        "toString" -> "DateUtilStub"
        "hashCode" -> System.identityHashCode(args)
        "equals" -> false
        else -> defaultValue(method.returnType)
    }
}

private fun <T> proxy(type: Class<T>, handler: (Method, Array<out Any?>?) -> Any?): T {
    @Suppress("UNCHECKED_CAST")
    val instance = Proxy.newProxyInstance(type.classLoader, arrayOf(type)) { _, method, args -> handler(method, args) }
    return instance as T
}

internal fun defaultValue(type: Class<*>): Any? = when (type) {
    java.lang.Boolean.TYPE, java.lang.Boolean::class.java -> false
    java.lang.Integer.TYPE, java.lang.Integer::class.java -> 0
    java.lang.Long.TYPE, java.lang.Long::class.java -> 0L
    java.lang.Double.TYPE, java.lang.Double::class.java -> 0.0
    java.lang.Float.TYPE, java.lang.Float::class.java -> 0f
    java.lang.Short.TYPE, java.lang.Short::class.java -> 0.toShort()
    java.lang.Byte.TYPE, java.lang.Byte::class.java -> 0.toByte()
    java.lang.Character.TYPE, java.lang.Character::class.java -> ' '
    else -> null
}

object KeyDefaults {

    fun keyOf(item: Any?): String? = when (item) {
        null -> null
        is String -> null
        is NonPreferenceKey -> item.key
        else -> runCatching {
            item.javaClass.getMethod("getKey").invoke(item) as String
        }.getOrNull()
    }

    fun defaultOf(item: Any?): Any? = runCatching {
        item?.javaClass?.getMethod("getDefaultValue")?.invoke(item)
    }.getOrNull()
}

class SimPreferences(
    private val values: MutableMap<String, Any?> = mutableMapOf(),
    private val logger: AAPSLogger = ConsoleLogger(),
) {

    fun asPreferences(): Preferences = proxy(Preferences::class.java) { method, args ->
        when (method.name) {
            "get" -> when {
                method.returnType == app.aaps.core.keys.interfaces.NonPreferenceKey::class.java -> null
                args == null || args.isEmpty() -> defaultValue(method.returnType)
                else -> read(args[0], method.returnType)
            }
            "getIfExists" -> {
                val key = KeyDefaults.keyOf(args?.getOrNull(0))
                if (key != null && values.containsKey(key)) read(args?.getOrNull(0), method.returnType) else null
            }
            "put" -> {
                val key = KeyDefaults.keyOf(args?.getOrNull(0))
                if (key != null && args != null) values[key] = args.last()
                Unit
            }
            "observe" -> {
                val value = read(args?.getOrNull(0), genericArg(method))
                MutableStateFlow(value)
            }
            "remove" -> Unit
            "registerPreferences", "getAllRegisteredKeys", "getSyncKeys" -> emptyList<PreferenceItem>()
            "getRaw" -> read(args?.getOrNull(0), method.returnType)
            "toString" -> "SimPreferences(${values.size})"
            "hashCode" -> System.identityHashCode(args)
            "equals" -> false
            else -> when {
                method.returnType == java.lang.Boolean.TYPE -> false
                method.returnType == java.lang.Integer.TYPE -> 0
                method.returnType == java.lang.Long.TYPE -> 0L
                method.returnType == java.lang.Double.TYPE -> 0.0
                else -> defaultValue(method.returnType)
            }
        }
    }

    private fun genericArg(method: Method): Class<*> =
        method.genericReturnType.typeName.substringAfterLast('<').substringBefore('>').let {
            when (it) {
                "java.lang.Boolean" -> java.lang.Boolean::class.java
                "java.lang.Integer", "kotlin.Int" -> java.lang.Integer::class.java
                "java.lang.Long", "kotlin.Long" -> java.lang.Long::class.java
                "java.lang.Double", "kotlin.Double" -> java.lang.Double::class.java
                "java.lang.String", "kotlin.String" -> java.lang.String::class.java
                else -> java.lang.Object::class.java
            }
        }

    private fun read(keyItem: Any?, type: Class<*>): Any? {
        val key = KeyDefaults.keyOf(keyItem)
        val raw = if (key != null && values.containsKey(key)) values[key] else KeyDefaults.defaultOf(keyItem)
        return convert(raw, type)
    }

    private fun convert(raw: Any?, type: Class<*>): Any? {
        if (raw == null) return defaultValue(type)
        return when (type) {
            java.lang.Boolean.TYPE, java.lang.Boolean::class.java -> when (raw) {
                is Boolean -> raw
                is Number -> raw.toInt() != 0
                is String -> raw.equals("true", ignoreCase = true)
                else -> false
            }
            java.lang.Integer.TYPE, java.lang.Integer::class.java ->
                (raw as? Number)?.toInt() ?: (raw as? String)?.toIntOrNull() ?: 0
            java.lang.Long.TYPE, java.lang.Long::class.java ->
                (raw as? Number)?.toLong() ?: (raw as? String)?.toLongOrNull() ?: 0L
            java.lang.Double.TYPE, java.lang.Double::class.java ->
                (raw as? Number)?.toDouble() ?: (raw as? String)?.toDoubleOrNull() ?: 0.0
            java.lang.String::class.java -> raw.toString()
            else -> raw
        }
    }
}

fun profileUtilProxy(): app.aaps.core.interfaces.profile.ProfileUtil =
    proxy(app.aaps.core.interfaces.profile.ProfileUtil::class.java) { method, args ->
        when (method.name) {
            "getUnits" -> GlucoseUnit.MGDL
            "getUnitLabel" -> "mg/dL"
            "fromMgdlToUnits" -> (args?.getOrNull(0) as? Double) ?: 0.0
            "fromMmolToUnits" -> ((args?.getOrNull(0) as? Double) ?: 0.0) * 18.0
            "valueInCurrentUnitsDetect" -> (args?.getOrNull(0) as? Double) ?: 0.0
            "fromMgdlToStringInUnits" -> (args?.getOrNull(0) as? Double)?.let { String.format(Locale.US, "%.0f", it) } ?: ""
            "fromMgdlToStringWithUnits" -> (args?.getOrNull(0) as? Double)?.let { String.format(Locale.US, "%.0f mg/dl", it) } ?: ""
            "fromMgdlToRoundString", "fromMgdlToRoundedString" ->
                (args?.getOrNull(0) as? Double)?.let { String.format(Locale.US, "%.0f", it) } ?: ""
            "toString" -> "ProfileUtilStub"
            "hashCode" -> System.identityHashCode(args)
            "equals" -> false
            else -> defaultValue(method.returnType)
        }
    }

fun textResolverProxy(): TextResolver = proxy(TextResolver::class.java) { method, args ->
    when (method.name) {
        "gs", "stringOrNull" -> args?.firstOrNull()?.toString() ?: ""
        "toString" -> "TextResolverStub"
        "hashCode" -> System.identityHashCode(args)
        "equals" -> false
        else -> defaultValue(method.returnType)
    }
}

fun anyProxy(type: Class<*>): Any = Proxy.newProxyInstance(type.classLoader, arrayOf(type), InvocationHandler { proxy, method, args ->
    when (method.name) {
        "toString" -> "${type.simpleName}Stub"
        "hashCode" -> System.identityHashCode(proxy)
        "equals" -> false
        else -> defaultValue(method.returnType)
    }
})
