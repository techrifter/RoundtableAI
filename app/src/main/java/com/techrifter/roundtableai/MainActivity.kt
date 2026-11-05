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
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.techrifter.roundtableai.auth.AuthState
import com.techrifter.roundtableai.auth.AuthViewModel
import com.techrifter.roundtableai.auth.SignInScreen
import com.techrifter.roundtableai.interview.InterviewScreen
import com.techrifter.roundtableai.ui.theme.RoundtableAITheme

private const val MIN_SPLASH_DURATION_MS = 500L

class MainActivity : ComponentActivity() {

    // Scoped to the Activity so Credential Manager can use it as the host context.
    private val authViewModel: AuthViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        val splashScreen = installSplashScreen()
        super.onCreate(savedInstanceState)

        val splashStartTime = SystemClock.elapsedRealtime()
        // Keep the splash visible while auth state is still Loading (checking persisted session).
        splashScreen.setKeepOnScreenCondition {
            val elapsed = SystemClock.elapsedRealtime() - splashStartTime
            val authLoading = authViewModel.authState.value is AuthState.Loading
            elapsed < MIN_SPLASH_DURATION_MS || authLoading
        }

        enableEdgeToEdge()
        setContent {
            RoundtableAITheme {
                val authState by authViewModel.authState.collectAsState()
                when (authState) {
                    is AuthState.SignedIn ->
                        InterviewScreen(
                            authViewModel = authViewModel,
                            modifier = Modifier.fillMaxSize(),
                        )
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
