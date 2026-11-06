# =============================================================================
# RoundtableAI ProGuard / R8 rules
# =============================================================================
# This app uses:
#   - Agora RTC SDK (io.agora.rtc2.*) — native JNI bridge; reflection-sensitive
#   - Agora RTM SDK (io.agora.rtm.*) — native JNI bridge
#   - Agora Conversational AI Agent Toolkit (io.agora.conversational.*)
#   - OkHttp 4.x
#   - Kotlin coroutines
#   - org.json.* (Android built-in; not shrunk, but listed for clarity)
#   - Pure Kotlin data classes parsed manually (no Gson/Moshi reflection)
#   - Firebase Auth + Credential Manager
#
# Serialization note: all JSON parsing uses org.json.JSONObject/JSONArray with
# explicit field access — there is NO reflection-based serialization (Gson,
# Moshi, kotlinx.serialization). Data classes do NOT need @Keep.
# =============================================================================

# ---------------------------------------------------------------------------
# Preserve stack traces in crash reports (Crashlytics / Play Console)
# ---------------------------------------------------------------------------
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# ---------------------------------------------------------------------------
# Agora RTC SDK
# Agora's native layer calls back into Java/Kotlin via JNI and reflection.
# Their own consumer rules cover most of this, but keeping the public surface
# explicitly guards against R8's aggressive inlining/merging passes.
# ---------------------------------------------------------------------------
-keep class io.agora.rtc2.** { *; }
-keep interface io.agora.rtc2.** { *; }
-keep class io.agora.base.** { *; }
-keep class io.agora.rtc.** { *; }      # legacy package still present in SDK internals

# ---------------------------------------------------------------------------
# Agora RTM SDK
# ---------------------------------------------------------------------------
-keep class io.agora.rtm.** { *; }
-keep interface io.agora.rtm.** { *; }

# ---------------------------------------------------------------------------
# Agora Conversational AI Agent Toolkit
# The toolkit ships without consumer ProGuard rules as of 0.4.x; keep fully.
# ---------------------------------------------------------------------------
-keep class io.agora.conversational.** { *; }
-keep interface io.agora.conversational.** { *; }

# ---------------------------------------------------------------------------
# OkHttp 4.x + Okio
# OkHttp ships its own consumer rules via META-INF; these are belt-and-suspenders
# for the response/request types we reference directly in SessionApi.
# ---------------------------------------------------------------------------
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.internal.publicsuffix.PublicSuffixDatabase { *; }

# ---------------------------------------------------------------------------
# Firebase Auth + Google Sign-In / Credential Manager
# ---------------------------------------------------------------------------
-keep class com.google.firebase.** { *; }
-keep class com.google.android.gms.** { *; }
-keep class com.google.android.libraries.identity.googleid.** { *; }
-keep class androidx.credentials.** { *; }
-keepattributes Signature

# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------
# Coroutines — keep the DebugMetadata annotation and internal machinery
-keepclassmembernames class kotlinx.** {
    volatile <fields>;
}
-keep class kotlin.coroutines.Continuation
-dontwarn kotlinx.coroutines.**

# Kotlin metadata — needed for inline functions and reified generics
-keepattributes RuntimeVisibleAnnotations
-keepattributes *Annotation*
-keep class kotlin.Metadata { *; }

# ---------------------------------------------------------------------------
# Android / Jetpack
# ---------------------------------------------------------------------------
# ViewModel constructors are called reflectively by ViewModelProvider
-keepclassmembers class * extends androidx.lifecycle.ViewModel {
    <init>(...);
}

# Compose keeps its own rules via the compiler plugin; nothing extra needed.

# ---------------------------------------------------------------------------
# Suppress warnings from SDK internals we don't control
# ---------------------------------------------------------------------------
-dontwarn com.google.android.gms.**
-dontwarn javax.annotation.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
