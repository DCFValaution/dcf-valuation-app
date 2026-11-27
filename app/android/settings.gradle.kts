pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "9.1.0" apply false
    id("org.jetbrains.kotlin.android") version "2.4.0" apply false
    // Reads app/google-services.json and generates the resources
    // firebase_core looks up at startup. Declared here, applied in
    // app/build.gradle.kts, which is what FlutterFire documents.
    id("com.google.gms.google-services") version "4.4.3" apply false
}

// A plugin can go missing from .flutter-plugins-dependencies, and when it
// does, nothing in the build complains. The Dart compiles, Gradle links, the
// APK installs and launches, the app values a company - and the moment it
// reaches Firebase the method channel has nobody on the Android side:
//
//     PlatformException(channel-error, Unable to establish connection on
//     channel: "...FirebaseCoreHostApi.initializeCore")
//
// so the account service is null and the only thing the reader sees is
// "Signing in is unavailable just now". That shipped twice before this
// check existed.
//
// GeneratedPluginRegistrant.java is generated FROM that file on every build,
// so it repairs itself and is useless as a signal. The file does not repair
// itself; only `flutter pub get` rewrites it.
//
// This runs while Gradle is still reading settings - before a line is
// compiled, and long before anything is installed - so a stripped build
// fails in seconds instead of producing an app that breaks only at runtime.
// Same list as tools/preflight.py; a plugin added later belongs in both.
run {
    val required =
        mapOf(
            "firebase_core" to "Firebase will not start at all",
            "firebase_auth" to "nobody can sign in",
            "cloud_firestore" to "no watchlist and no saved valuations",
            "google_sign_in_android" to "the Android sign-in sheet never opens",
            "firebase_app_check" to
                "nothing is attested, so every request is refused once App Check is on",
        )

    val deps = file("../.flutter-plugins-dependencies")
    require(deps.exists()) {
        "${deps.path} is missing. Fix: cd app && flutter pub get"
    }

    @Suppress("UNCHECKED_CAST")
    val parsed = groovy.json.JsonSlurper().parseText(deps.readText()) as Map<String, Any?>

    @Suppress("UNCHECKED_CAST")
    val androidPlugins =
        (parsed["plugins"] as Map<String, Any?>)["android"] as List<Map<String, Any?>>
    val present = androidPlugins.map { it["name"] as String }.toSet()
    val missing = required.keys.filter { it !in present }.sorted()

    // The cause is not known yet, and `flutter pub get` - the fix - destroys
    // every file that would identify it. So take the evidence first, while it
    // is still on disk, without needing anyone to remember to.
    val archived =
        if (missing.isEmpty()) {
            null
        } else {
            try {
                val stamp =
                    java.time.LocalDateTime.now()
                        .format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"))
                val into = file("../../strip-evidence/$stamp")
                into.mkdirs()
                listOf(
                    deps,
                    file("../.dart_tool/package_graph.json"),
                    file("../.dart_tool/package_config.json"),
                    file("../pubspec.yaml"),
                    file("../pubspec.lock"),
                ).filter { it.exists() }
                    .forEach { it.copyTo(File(into, it.name), overwrite = true) }

                // Timestamps say whether pub ran, which the contents cannot.
                File(into, "mtimes.txt").writeText(
                    buildString {
                        appendLine("captured: $stamp")
                        appendLine("missing: ${missing.joinToString(", ")}")
                        listOf(
                            deps,
                            file("../.dart_tool/package_graph.json"),
                            file("../.dart_tool/package_config.json"),
                            file("../pubspec.yaml"),
                        ).forEach {
                            appendLine("${it.name}: ${java.util.Date(it.lastModified())}")
                        }
                    },
                )
                into.path
            } catch (e: Exception) {
                "could not archive: $e"
            }
        }

    require(missing.isEmpty()) {
        buildString {
            appendLine()
            appendLine(".flutter-plugins-dependencies lists no Android build for:")
            missing.forEach { appendLine("  - $it - without it, ${required[it]}") }
            appendLine()
            appendLine("Their method channels would have no Android implementation.")
            appendLine("The app would compile, install and launch, and fail only on")
            appendLine("reaching Firebase - which is far too late to find out.")
            appendLine()
            appendLine("Evidence saved (pub get would erase it): $archived")
            appendLine()
            appendLine("Fix: cd app && flutter pub get   (then build again)")
        }
    }
}

include(":app")
