package com.techrifter.roundtableai.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import com.techrifter.roundtableai.BuildConfig

data class AgoraCredentials(
    val appId: String = "",
    val appCertificate: String = "",
    val customerId: String = "",
    val customerSecret: String = "",
    val groqApiKey: String = "",
)

data class ConnectionStatus(
    val status: String,
    val appId: String = "",
    val appCertificate: String = "",
    val customerId: String = "",
    val customerSecret: String = "",
    val groqApiKey: String = "",
)

class SettingsApi(
    private val tokenProvider: suspend () -> String?
) {
    private val client = OkHttpClient.Builder().build()
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    private suspend fun authedRequest(url: String): Request.Builder {
        val token = tokenProvider()
        return Request.Builder()
            .url(url)
            .apply { if (token != null) header("Authorization", "Bearer $token") }
    }

    suspend fun getStatus(): ConnectionStatus = withContext(Dispatchers.IO) {
        val request = authedRequest("${BuildConfig.BACKEND_BASE_URL}/settings/agora/status").get().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                return@withContext ConnectionStatus("Not configured")
            }
            val json = JSONObject(response.body?.string().orEmpty())
            return@withContext ConnectionStatus(
                status = json.optString("connection_status", "Not configured"),
                appId = json.optString("app_id", ""),
                appCertificate = json.optString("app_certificate", ""),
                customerId = json.optString("customer_id", ""),
                customerSecret = json.optString("customer_secret", ""),
                groqApiKey = json.optString("groq_api_key", ""),
            )
        }
    }

    suspend fun saveCredentials(credentials: AgoraCredentials): Result<Unit> = withContext(Dispatchers.IO) {
        val payload = JSONObject().apply {
            put("app_id", credentials.appId)
            put("app_certificate", credentials.appCertificate)
            put("customer_id", credentials.customerId)
            put("customer_secret", credentials.customerSecret)
            put("groq_api_key", credentials.groqApiKey)
        }.toString().toRequestBody(jsonMediaType)
        
        val request = authedRequest("${BuildConfig.BACKEND_BASE_URL}/settings/agora/credentials")
            .post(payload)
            .build()
            
        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val errorBody = response.body?.string()
                    val errorMsg = try {
                        JSONObject(errorBody.orEmpty()).optString("detail", "Validation failed")
                    } catch (e: Exception) {
                        "Validation failed"
                    }
                    return@withContext Result.failure(Exception(errorMsg))
                }
                return@withContext Result.success(Unit)
            }
        } catch (e: IOException) {
            return@withContext Result.failure(Exception("Network error"))
        }
    }

    suspend fun removeCredentials(): Result<Unit> = withContext(Dispatchers.IO) {
        val request = authedRequest("${BuildConfig.BACKEND_BASE_URL}/settings/agora/credentials")
            .delete()
            .build()
            
        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@withContext Result.failure(Exception("Failed to remove credentials"))
                }
                return@withContext Result.success(Unit)
            }
        } catch (e: IOException) {
            return@withContext Result.failure(Exception("Network error"))
        }
    }
}
