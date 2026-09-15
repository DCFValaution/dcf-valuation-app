import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing secrets. android/key.properties is git-ignored; see
// android/key.properties.example for its shape.
val keystorePropertiesFile = rootProject.file("key.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        FileInputStream(keystorePropertiesFile).use { load(it) }
    }
}

android {
    namespace = "com.intrinsic.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // The identity Android and the Play Store know the app by. Changing it
        // after publishing would create a separate app rather than an update,
        // so it is fixed from here on.
        applicationId = "com.intrinsic.app"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        // Populated only when key.properties exists. The file holds the
        // keystore passwords and is git-ignored; it lives on the developer's
        // machine and nowhere else.
        if (keystorePropertiesFile.exists()) {
            create("release") {
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            // Deliberately no fallback to the debug key. An APK signed with the
            // debug key cannot later be upgraded by a properly signed one, so
            // shipping one by accident is worse than a build that refuses.
            if (keystorePropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }
}

// Fail release builds loudly when signing is not configured, rather than
// producing an unsigned or debug-signed APK. Debug builds are unaffected.
tasks.configureEach {
    if (name.contains("Release") && !keystorePropertiesFile.exists()) {
        doFirst {
            throw GradleException(
                "Release signing is not configured: ${keystorePropertiesFile.path} " +
                "is missing. Copy android/key.properties.example to " +
                "android/key.properties and fill it in."
            )
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
