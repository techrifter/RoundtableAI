package com.techrifter.roundtableai.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.techrifter.roundtableai.network.AgoraCredentials
import com.techrifter.roundtableai.network.SettingsApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class SettingsUiState {
    object Loading : SettingsUiState()
    data class Success(
        val status: String,
        val credentials: AgoraCredentials? = null
    ) : SettingsUiState()
    data class Error(val message: String) : SettingsUiState()
}

class SettingsViewModel(private val settingsApi: SettingsApi) : ViewModel() {
    private val _uiState = MutableStateFlow<SettingsUiState>(SettingsUiState.Loading)
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        fetchStatus()
    }

    fun fetchStatus() {
        viewModelScope.launch {
            _uiState.value = SettingsUiState.Loading
            try {
                val conn = settingsApi.getStatus()
                val creds = if (conn.appId.isNotEmpty() || conn.appCertificate.isNotEmpty() || conn.groqApiKey.isNotEmpty()) {
                    AgoraCredentials(
                        appId = conn.appId,
                        appCertificate = conn.appCertificate,
                        customerId = conn.customerId,
                        customerSecret = conn.customerSecret,
                        groqApiKey = conn.groqApiKey,
                    )
                } else null
                _uiState.value = SettingsUiState.Success(conn.status, creds)
            } catch (e: Exception) {
                _uiState.value = SettingsUiState.Error("Failed to fetch connection status")
            }
        }
    }

    fun saveCredentials(
        appId: String,
        appCert: String,
        customerId: String,
        customerSecret: String,
        groqApiKey: String = "",
        onSuccess: (() -> Unit)? = null,
    ) {
        viewModelScope.launch {
            _uiState.value = SettingsUiState.Loading
            try {
                val creds = AgoraCredentials(appId, appCert, customerId, customerSecret, groqApiKey)
                val result = settingsApi.saveCredentials(creds)
                if (result.isSuccess) {
                    _uiState.value = SettingsUiState.Success("Connected", creds)
                    onSuccess?.invoke()
                } else {
                    _uiState.value = SettingsUiState.Error(result.exceptionOrNull()?.message ?: "Validation failed")
                }
            } catch (e: Exception) {
                _uiState.value = SettingsUiState.Error("Failed to save credentials")
            }
        }
    }

    fun removeCredentials() {
        viewModelScope.launch {
            _uiState.value = SettingsUiState.Loading
            try {
                val result = settingsApi.removeCredentials()
                if (result.isSuccess) {
                    _uiState.value = SettingsUiState.Success("Not configured")
                } else {
                    _uiState.value = SettingsUiState.Error("Failed to remove credentials")
                }
            } catch (e: Exception) {
                _uiState.value = SettingsUiState.Error("Failed to remove credentials")
            }
        }
    }
}
