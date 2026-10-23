plugins {
    id("com.android.application")
}

android {
    namespace = "com.artyonix.murcloud"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.artyonix.murcloud"
        minSdk = 26
        targetSdk = 36
        versionCode = 100000
        versionName = "10.0.0-beta"
        // Default server suggested on the connection screen (override: -PmurServer=https://...)
        buildConfigField("String", "DEFAULT_SERVER", "\"${project.findProperty("murServer") ?: ""}\"")
    }

    // Release signing comes from environment variables (CI secrets); nothing is stored in the repo.
    val ks = System.getenv("MUR_KEYSTORE_FILE")
    signingConfigs {
        if (ks != null) {
            create("release") {
                storeFile = file(ks)
                storePassword = System.getenv("MUR_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MUR_KEY_ALIAS")
                keyPassword = System.getenv("MUR_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (ks != null) signingConfig = signingConfigs.getByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        warningsAsErrors = false
    }
}

dependencies {
    // Align transitive kotlin-stdlib-jdk7/jdk8 with kotlin-stdlib (avoids duplicate classes).
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.8.22"))
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.14.0")
    testImplementation("junit:junit:4.13.2")
}
