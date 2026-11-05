package com.techrifter.roundtableai.network

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import com.techrifter.roundtableai.BuildConfig
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

// Backend base URL is configured in build.gradle and injected into BuildConfig at build time.
private val BACKEND_BASE_URL = BuildConfig.BACKEND_BASE_URL

private val BACKEND_IS_RENDER = BACKEND_BASE_URL.contains(".onrender.com")

private const val WARM_UP_ATTEMPTS = 4
private const val WARM_UP_RETRY_DELAY_MS = 2_000L
private const val WARM_UP_WAIT_MS = 90_000L
private const val LIMITS_REQUEST_ATTEMPTS = 3
private const val LIMITS_RETRY_DELAY_MS = 1_500L

private const val BACKEND_UNREACHABLE =
    "Can't reach the interview service right now. Try again after some time."

/** If the backend returns a 4xx or 5xx, try to extract a candidate-facing error message
 */
private fun Response.candidateFacingError(fallback: String): String {
    val detail = runCatching { JSONObject(body?.string().orEmpty()).getString("detail") }.getOrNull()
    if (!detail.isNullOrBlank() && detail.length <= 200) return detail
    return if (code >= 500 || code == 404) BACKEND_UNREACHABLE else fallback
}

/** Same idea for transport failures. UnknownHostException's message is the bare
 * hostname, which would otherwise leak the tunnel URL into the UI. */
private fun networkError(t: IOException): String = when (t) {
    is SocketTimeoutException -> "The interview service took too long to respond. Try again."
    else -> BACKEND_UNREACHABLE
}

private fun OkHttpClient.executeOrFail(request: Request): Response =
    try {
        newCall(request).execute()
    } catch (t: IOException) {
        error(networkError(t))
    }

data class SessionInfo(
    val appId: String,
    val channelName: String,
    val candidateUid: Int,
    val candidateToken: String,
    val rtmToken: String,
    val agentId: String,
)

data class EvidencePoint(val point: String, val evidence: String)

data class InterviewReport(
    val technical: Double,
    val systemDesign: Double,
    val behavioral: Double,
    val communication: Double,
    val overall: Double,
    val strengths: List<EvidencePoint>,
    val improvements: List<EvidencePoint>,
    val summary: String,
    val mode: String = "full",
    val targetSkill: String? = null,
) {
    constructor(
        technical: Double,
        systemDesign: Double,
        behavioral: Double,
        communication: Double,
        overall: Double,
        strengths: List<EvidencePoint>,
        improvements: List<EvidencePoint>,
        summary: String,
    ) : this(technical, systemDesign, behavioral, communication, overall, strengths, improvements, summary, "full", null)
}

data class ReportResult(val status: String, val report: InterviewReport?)

data class InterviewProfilePreview(
    val role: String,
    val skills: List<String>,
    val responsibilities: List<String>,
)

data class UploadResult(val profileId: String, val profile: InterviewProfilePreview)

data class UserLimits(
    val dailyLimit: Int = 2,
    val usedToday: Int = 0,
    val remaining: Int = 2,
    val canStart: Boolean = true,
    val hasJoinedWaitlist: Boolean = false,
)

/** Resolves a content:// Uri's display name (e.g. "Resume.pdf") — the backend
 * dispatches on the extension and the picker UI labels the button with it. Falls
 * back to a generic name when the provider doesn't expose one. */
fun resolveDisplayName(context: Context, uri: Uri): String {
    context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
        val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (nameIndex >= 0 && cursor.moveToFirst()) {
            cursor.getString(nameIndex)?.let { return it }
        }
    }
    return uri.lastPathSegment ?: "document"
}

/**
 * [tokenProvider] is a suspend function that returns the current Firebase ID token,
 * or null if the user is not signed in. Called before every request so the token
 * is always fresh (Firebase refreshes it automatically when < 5 min remain).
 */
class SessionApi(private val tokenProvider: suspend () -> String?) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    // Starts already complete off Render, so local development never waits on it.
    private val warmedUp = CompletableDeferred<Unit>().apply { if (!BACKEND_IS_RENDER) complete(Unit) }

    /** Builds a Request.Builder pre-populated with the Authorization header. */
    private suspend fun authedRequest(url: String): Request.Builder {
        val token = tokenProvider()
        return Request.Builder()
            .url(url)
            .apply { if (token != null) header("Authorization", "Bearer $token") }
    }

    /** Wakes a sleeping instance. */
    suspend fun warmUp() = withContext(Dispatchers.IO) {
        if (warmedUp.isCompleted) return@withContext
        val request = Request.Builder().url("$BACKEND_BASE_URL/health").get().build()
        for (attempt in 1..WARM_UP_ATTEMPTS) {
            val awake = try {
                client.newCall(request).execute().use { it.isSuccessful }
            } catch (t: IOException) {
                false
            }
            if (awake) break
            delay(WARM_UP_RETRY_DELAY_MS)
        }
        // Completed even if it never answered, so the upload is never blocked.
        warmedUp.complete(Unit)
    }

    /** Fetches the candidate's current session limits and remaining interviews in the last 24 hours. */
    suspend fun getUserLimits(): UserLimits? = withContext(Dispatchers.IO) {
        // The dashboard may request limits before the startup warm-up completes.
        withTimeoutOrNull(WARM_UP_WAIT_MS) { warmedUp.await() }
        val request = authedRequest("$BACKEND_BASE_URL/session/limits").get().build()
        repeat(LIMITS_REQUEST_ATTEMPTS) { attempt ->
            try {
                client.newCall(request).execute().use { response ->
                    if (response.code in 500..599 && attempt < LIMITS_REQUEST_ATTEMPTS - 1) {
                        android.util.Log.w(
                            "SessionApi",
                            "Limits request returned HTTP ${response.code}; retrying",
                        )
                    } else if (!response.isSuccessful) {
                        android.util.Log.w("SessionApi", "Limits request failed: HTTP ${response.code}")
                        return@withContext null
                    } else {
                        val json = JSONObject(response.body?.string().orEmpty())
                        val requiredFields = listOf(
                            "daily_limit",
                            "used_today",
                            "remaining",
                            "can_start",
                            "has_joined_waitlist",
                        )
                        if (requiredFields.any { !json.has(it) || json.isNull(it) }) {
                            android.util.Log.e("SessionApi", "Limits response is missing required fields")
                            return@withContext null
                        }
                        return@withContext UserLimits(
                            dailyLimit = json.getInt("daily_limit"),
                            usedToday = json.getInt("used_today"),
                            remaining = json.getInt("remaining"),
                            canStart = json.getBoolean("can_start"),
                            hasJoinedWaitlist = json.getBoolean("has_joined_waitlist"),
                        )
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: IOException) {
                android.util.Log.w(
                    "SessionApi",
                    "Limits request attempt ${attempt + 1} failed due to network error",
                    e,
                )
                if (attempt == LIMITS_REQUEST_ATTEMPTS - 1) return@withContext null
            } catch (e: Exception) {
                android.util.Log.e("SessionApi", "Could not read limits response", e)
                return@withContext null
            }
            delay(LIMITS_RETRY_DELAY_MS * (attempt + 1))
        }
        null
    }

    /** Adds the candidate's verified account to the Pro early-access waitlist. */
    suspend fun joinWaitlist(): Boolean = withContext(Dispatchers.IO) {
        try {
            val request = authedRequest("$BACKEND_BASE_URL/user/waitlist")
                .post("{}".toRequestBody("application/json".toMediaType()))
                .build()
            client.newCall(request).execute().use { response ->
                android.util.Log.d("SessionApi", "joinWaitlist HTTP code: ${response.code}")
                response.isSuccessful
            }
        } catch (t: Throwable) {
            android.util.Log.e("SessionApi", "joinWaitlist error: ${t.message}", t)
            false
        }
    }

    /** Submits an authenticated account-deletion request to the backend queue. */
    suspend fun deleteAccount() = withContext(Dispatchers.IO) {
        val request = authedRequest("$BACKEND_BASE_URL/user/account")
            .delete()
            .build()
        client.executeOrFail(request).use { response ->
            if (!response.isSuccessful) {
                error(response.candidateFacingError("Couldn't delete the account. Please try again."))
            }
        }
    }

    /** Uploads the candidate's JD and/or resume — either one alone is enough, returns the
     * resulting profile_id to pass to startSession. Parsing happens once up
     * front so the app can show the candidate what the panel actually
     * understood before joining the call. */
    suspend fun uploadJd(context: Context, jdUri: Uri?, resumeUri: Uri?): UploadResult =
        withContext(Dispatchers.IO) {
            // Wait for the warm-up to finish, but don't block forever if it never does.
            withTimeoutOrNull(WARM_UP_WAIT_MS) { warmedUp.await() }

            val bodyBuilder = MultipartBody.Builder().setType(MultipartBody.FORM)
            if (jdUri != null) addFilePart(bodyBuilder, context, "jd_file", jdUri)
            if (resumeUri != null) addFilePart(bodyBuilder, context, "resume_file", resumeUri)
            val request = authedRequest("$BACKEND_BASE_URL/jd/upload")
                .post(bodyBuilder.build())
                .build()

            client.executeOrFail(request).use { response ->
                if (!response.isSuccessful) {
                    error(response.candidateFacingError("Couldn't read that JD/resume."))
                }
                val json = JSONObject(response.body?.string().orEmpty())
                val profileJson = json.getJSONObject("profile")
                UploadResult(
                    profileId = json.getString("profile_id"),
                    profile = InterviewProfilePreview(
                        role = profileJson.getString("role"),
                        skills = profileJson.getJSONArray("skills").toStringList(),
                        responsibilities = profileJson.getJSONArray("responsibilities").toStringList(),
                    ),
                )
            }
        }

    private fun addFilePart(builder: MultipartBody.Builder, context: Context, field: String, uri: Uri) {
        val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            ?: error("Couldn't read the selected file")
        val filename = resolveDisplayName(context = context, uri = uri)
        val mediaType = context.contentResolver.getType(uri)?.toMediaType()
        builder.addFormDataPart(field, filename, bytes.toRequestBody(mediaType))
    }

    suspend fun startSession(profileId: String): SessionInfo =
        startSession(profileId, "full", null)

    suspend fun startSession(
        profileId: String,
        mode: String = "full",
        targetSkill: String? = null,
    ): SessionInfo = withContext(Dispatchers.IO) {
        val payload = JSONObject().apply {
            put("profile_id", profileId)
            put("mode", mode)
            if (!targetSkill.isNullOrBlank()) {
                put("target_skill", targetSkill)
            }
        }.toString()
        val request = authedRequest("$BACKEND_BASE_URL/session/start")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()

        client.executeOrFail(request).use { response ->
            if (!response.isSuccessful) {
                error(response.candidateFacingError("Couldn't start the interview. Try again."))
            }
            val json = JSONObject(response.body?.string().orEmpty())
            SessionInfo(
                appId = json.getString("app_id"),
                channelName = json.getString("channel_name"),
                candidateUid = json.getInt("candidate_uid"),
                candidateToken = json.getString("candidate_token"),
                rtmToken = json.getString("rtm_token"),
                agentId = json.getString("agent_id"),
            )
        }
    }

    /** Polled during a live call to find out which persona is currently
     * interviewing. Returns null if the backend hasn't recorded a turn yet
     * (e.g. right at call start) or the request fails. */
    suspend fun getCurrentStage(channelName: String): String? = withContext(Dispatchers.IO) {
        val request = authedRequest("$BACKEND_BASE_URL/session/$channelName/stage")
            .get()
            .build()

        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val json = JSONObject(response.body?.string().orEmpty())
                if (json.isNull("stage")) null else json.getString("stage")
            }
        } catch (t: Throwable) {
            null
        }
    }

    /** Called the moment the candidate taps End, regardless of interview stage —
     * starts scoring whatever was actually covered so far if it hasn't already
     * started. Best-effort: a failure here just means the first getReport() poll
     * does the same job (or the interview reached FINAL_EVAL and already
     * triggered it server-side on its own). */
    suspend fun endSession(channelName: String): ReportResult? = withContext(Dispatchers.IO) {
        val request = authedRequest("$BACKEND_BASE_URL/session/$channelName/end")
            .post("".toRequestBody("application/json".toMediaType()))
            .build()
        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) null else parseReportResult(response.body?.string().orEmpty())
            }
        } catch (t: Throwable) {
            null
        }
    }

    /** Polled after the interview ends. Report generation runs server-side as a
     * background task once the panel reaches its final stage, so this is usually
     * already "pending" or "ready" by the time the candidate disconnects. */
    suspend fun getReport(channelName: String): ReportResult? = withContext(Dispatchers.IO) {
        val request = authedRequest("$BACKEND_BASE_URL/session/$channelName/report")
            .get()
            .build()

        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) null else parseReportResult(response.body?.string().orEmpty())
            }
        } catch (t: Throwable) {
            null
        }
    }

    private fun parseReportResult(body: String): ReportResult {
        val json = JSONObject(body)
        val status = json.getString("status")
        val report = if (json.isNull("report")) null else {
            val r = json.getJSONObject("report")
            InterviewReport(
                technical = r.getDouble("technical"),
                systemDesign = r.getDouble("system_design"),
                behavioral = r.getDouble("behavioral"),
                communication = r.getDouble("communication"),
                overall = r.getDouble("overall"),
                strengths = r.getJSONArray("strengths").let { arr ->
                    (0 until arr.length()).map { i ->
                        val o = arr.getJSONObject(i)
                        EvidencePoint(o.getString("point"), o.getString("evidence"))
                    }
                },
                improvements = r.getJSONArray("improvements").let { arr ->
                    (0 until arr.length()).map { i ->
                        val o = arr.getJSONObject(i)
                        EvidencePoint(o.getString("point"), o.getString("evidence"))
                    }
                },
                summary = r.getString("summary"),
                mode = r.optString("mode", "full"),
                targetSkill = if (r.isNull("target_skill") || r.optString("target_skill").isBlank()) null else r.optString("target_skill"),
            )
        }
        return ReportResult(status = status, report = report)
    }
}

private fun JSONArray.toStringList(): List<String> = (0 until length()).map { getString(it) }
