plugins {
    kotlin("jvm")
}

dependencies {
    implementation(project(":core:data"))
    implementation(project(":core:interfaces"))
    implementation(project(":core:keys"))
    implementation(project(":core:objects"))
    implementation(project(":core:utils"))
    implementation(project(":plugins:aps"))
    implementation(project(":plugins:main"))
    implementation(project(":plugins:smoothing"))
    implementation(project(":workflow"))
    implementation(project(":implementation"))
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.datetime)
}

kotlin {
    jvmToolchain(21)
}

tasks.register<JavaExec>("bridge") {
    group = "simulation"
    description = "Run the W1 replay bridge"
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("app.aaps.sim.SimBridgeKt")
    standardInput = System.`in`
}

tasks.register("simClasspath") {
    group = "simulation"
    description = "Write the bridge runtime classpath to build/sim-classpath.txt"
    val runtime = sourceSets["main"].runtimeClasspath
    val output = layout.buildDirectory.file("sim-classpath.txt")
    inputs.files(runtime)
    outputs.file(output)
    doLast {
        val file = output.get().asFile
        file.parentFile.mkdirs()
        file.writeText(runtime.asPath)
    }
}
