plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "de.loresoft.youtubeclipster"
    compileSdk = 34

    defaultConfig {
        applicationId = "de.loresoft.youtubeclipster"
        minSdk = 24
        targetSdk = 34
        // Kept in step with clipster/__init__.py (APP_BUILD / APP_VERSION).
        // tests/test_version.py fails when the two drift apart: an APK that
        // reports a different version from the program inside it is worse than
        // no version at all.
        versionCode = 6
        versionName = "2.2.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
}
