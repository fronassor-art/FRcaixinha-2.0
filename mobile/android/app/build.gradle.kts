plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.frcaixinha.app"
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    kotlinOptions {
        jvmTarget = "21"
    }
    compileSdk = 36

    defaultConfig {
        applicationId = "com.frcaixinha.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 26
        versionName = "0.26.0"
    }

    signingConfigs {
        create("release") {
            storeFile = file("../../frcaixinha-release.keystore")
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias = System.getenv("KEY_ALIAS")
            keyPassword = System.getenv("KEY_PASSWORD")
        }
    }

    buildTypes {
        release { signingConfig = signingConfigs.getByName("release"); isMinifyEnabled = true; isShrinkResources = true; proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro") }
    }
}

flutter { source = "../.." }
