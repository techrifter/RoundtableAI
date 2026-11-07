import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    id("com.google.gms.google-services")
}

// Load the backend base URL from local.properties, or use a placeholder if not found.
val backendBaseUrl: String = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}.getProperty("backend.baseUrl") ?: "https://REPLACE_ME.invalid"

android {
    namespace = "com.techrifter.roundtableai"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "com.techrifter.roundtableai"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("String", "BACKEND_BASE_URL", "\"${backendBaseUrl.trimEnd('/')}\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            ndk {
                debugSymbolLevel = "FULL"
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }

}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.core.splashscreen)
    implementation(libs.androidx.lifecycle.runtime.ktx)

    implementation(libs.agora.rtc.sdk)
    implementation(libs.agora.rtm.sdk)
    implementation(libs.agora.agent.toolkit)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    // Firebase — versions managed by the BOM; do not specify versions individually.
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.auth)

    // Credential Manager for Google Sign-In (modern, non-deprecated API)
    implementation(libs.androidx.credentials)
    implementation(libs.androidx.credentials.play.services.auth)
    implementation(libs.google.identity.googleid)

    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
