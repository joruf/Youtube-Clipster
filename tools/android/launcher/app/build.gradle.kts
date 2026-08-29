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
        versionCode = (findProperty("CLIPSTER_VERSION_CODE") as String).toInt()
        versionName = findProperty("CLIPSTER_VERSION_NAME") as String
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
