package com.techrifter.roundtableai

import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import com.techrifter.roundtableai.interview.InterviewScreen
import com.techrifter.roundtableai.ui.theme.RoundtableAITheme

private const val MIN_SPLASH_DURATION_MS = 500L

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        val splashScreen = installSplashScreen()
        super.onCreate(savedInstanceState)

        val splashStartTime = SystemClock.elapsedRealtime()
        splashScreen.setKeepOnScreenCondition {
            SystemClock.elapsedRealtime() - splashStartTime < MIN_SPLASH_DURATION_MS
        }

        enableEdgeToEdge()
        setContent {
            RoundtableAITheme {
                InterviewScreen(modifier = Modifier.fillMaxSize())
            }
        }
    }
}
