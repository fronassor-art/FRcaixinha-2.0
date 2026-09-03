plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.frcaixinha.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.frcaixinha.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 26
        versionName = "0.26.0"
    }

    buildTypes {
        release { isMinifyEnabled = true; isShrinkResources = true; proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro") }
    }
}

flutter { source = "../.." }
