package com.techrifter.roundtableai

import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.techrifter.roundtableai.auth.AuthState
import com.techrifter.roundtableai.auth.AuthViewModel
import com.techrifter.roundtableai.auth.SignInScreen
import com.techrifter.roundtableai.interview.InterviewScreen
import com.techrifter.roundtableai.network.SettingsApi
import com.techrifter.roundtableai.settings.SettingsScreen
import com.techrifter.roundtableai.settings.SettingsViewModel
import com.techrifter.roundtableai.ui.theme.RoundtableAITheme

private const val MIN_SPLASH_DURATION_MS = 500L

class MainActivity : ComponentActivity() {

    private val authViewModel: AuthViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        val splashScreen = installSplashScreen()
        super.onCreate(savedInstanceState)
        
        val settingsApi = SettingsApi { authViewModel.getIdToken() }
        val settingsViewModel = SettingsViewModel(settingsApi)

        val splashStartTime = SystemClock.elapsedRealtime()
        splashScreen.setKeepOnScreenCondition {
            val elapsed = SystemClock.elapsedRealtime() - splashStartTime
            val authLoading = authViewModel.authState.value is AuthState.Loading
            elapsed < MIN_SPLASH_DURATION_MS || authLoading
        }

        enableEdgeToEdge()
        setContent {
            RoundtableAITheme {
                val authState by authViewModel.authState.collectAsState()
                var showSettings by remember { mutableStateOf(false) }

                when {
                    showSettings -> {
                        SettingsScreen(viewModel = settingsViewModel, onBack = { showSettings = false })
                    }
                    authState is AuthState.SignedIn -> {
                        InterviewScreen(
                            authViewModel = authViewModel,
                            onOpenSettings = { showSettings = true },
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                    else ->
                        SignInScreen(
                            authViewModel = authViewModel,
                            modifier = Modifier.fillMaxSize(),
                        )
                }
            }
        }
    }
}
