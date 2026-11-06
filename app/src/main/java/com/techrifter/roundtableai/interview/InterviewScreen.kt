package com.techrifter.roundtableai.interview

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.techrifter.roundtableai.R
import com.techrifter.roundtableai.network.EvidencePoint
import com.techrifter.roundtableai.network.InterviewProfilePreview
import com.techrifter.roundtableai.network.InterviewReport
import com.techrifter.roundtableai.network.UserLimits
import com.techrifter.roundtableai.network.resolveDisplayName
import io.agora.conversational.api.AgentState
import kotlin.math.sin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// Duotone by design: near-black plus one signature hue. Agent state is shown through
// the orb's brightness and motion, never by swapping hue — don't add per-state colors.
private val Backdrop = Color(0xFF0A0A12)
private val Panel = Color(0xFF181622)
private val Ink = Color(0xFFF6F4FF)
private val InkMuted = Color(0xFFACA6C4)
private val InkFaint = Color(0xFF625D7A)
private val SignatureA = Color(0xFF8B5CF6) // violet
private val SignatureB = Color(0xFFFF3DA6) // magenta
private val Signature = SignatureA // flat accent use (labels, rims) — gradient reserved for the orb/waveform
private val SignatureDim = Color(0xFF2B2044)
private val Danger = Color(0xFFFF4D6D)

@OptIn(ExperimentalTextApi::class)
internal val Inter = FontFamily(
    Font(R.font.inter_variable, weight = FontWeight.Normal, variationSettings = FontVariation.Settings(FontVariation.weight(400))),
    Font(R.font.inter_variable, weight = FontWeight.Medium, variationSettings = FontVariation.Settings(FontVariation.weight(500))),
    Font(R.font.inter_variable, weight = FontWeight.SemiBold, variationSettings = FontVariation.Settings(FontVariation.weight(600))),
    Font(R.font.inter_variable, weight = FontWeight.Bold, variationSettings = FontVariation.Settings(FontVariation.weight(700))),
    Font(R.font.inter_variable, weight = FontWeight.ExtraBold, variationSettings = FontVariation.Settings(FontVariation.weight(800))),
)
private val InterTabular = Inter // numerals read fine at these sizes without a separate tabular axis

/** How lit the orb's signature side is (0 = almost eclipsed, 1 = full burn) and
 * how much energy its terminator line and silhouette wobble carry — this, not a
 * hue change, is how the orb communicates agent state. */
private data class OrbEnergy(val brightness: Float, val wobble: Float)

private fun energyFor(state: AgentState): OrbEnergy = when (state) {
    AgentState.LISTENING -> OrbEnergy(0.55f, 0.35f)
    AgentState.THINKING -> OrbEnergy(0.7f, 0.55f)
    AgentState.SPEAKING -> OrbEnergy(1f, 1f)
    AgentState.SILENT -> OrbEnergy(0.3f, 0.15f)
    else -> OrbEnergy(0.12f, 0.08f)
}

private fun statusLineFor(state: AgentState): String = when (state) {
    AgentState.LISTENING -> "Listening"
    // A real interviewer doesn't narrate "reviewing your answer" after every
    // response — they just go quiet for a beat, then speak. "…" marks that pause
    AgentState.THINKING -> "…"
    AgentState.SPEAKING -> "Speaking"
    AgentState.SILENT -> "Ready when you are"
    else -> "Connecting"
}

private fun personaLabelFor(stage: String?): String = when (stage) {
    "technical" -> "Technical Interviewer"
    "system_design" -> "System Design Interviewer"
    "behavioral" -> "Behavioral Interviewer"
    "final_eval" -> "Wrapping up"
    else -> "Your interview panel"
}

// Phase progress, not a time estimate — turns per stage are adaptive (see
// orchestrator.py), so any countdown would be wrong as often as it's right.
private val STAGE_ORDER = listOf("technical", "system_design", "behavioral", "final_eval")

private fun stageIndexFor(stage: String?): Int {
    // Before the first stage poll resolves, the interview is still in the
    // technical round in practice — defaulting to 0 avoids a flash of "nothing
    // highlighted yet" on an otherwise-live screen.
    val index = STAGE_ORDER.indexOf(stage)
    return if (index == -1) 0 else index
}

@Composable
private fun StageProgress(currentStage: String?, modifier: Modifier = Modifier) {
    val activeIndex = stageIndexFor(currentStage)
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        STAGE_ORDER.forEachIndexed { index, _ ->
            val isActive = index == activeIndex
            val isComplete = index < activeIndex
            val fraction by animateFloatAsState(
                if (isComplete || isActive) 1f else 0f,
                tween(500),
                label = "segment$index",
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(SignatureDim),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth(fraction)
                        .fillMaxHeight()
                        .clip(RoundedCornerShape(2.dp))
                        .background(
                            if (isActive) {
                                Brush.horizontalGradient(listOf(SignatureA, SignatureB))
                            } else {
                                Brush.horizontalGradient(listOf(Signature, Signature))
                            }
                        ),
                )
            }
        }
    }
}

/**
 * The panel's presence: a dark disc, a glow ring pulsing outward (faster and
 * brighter with agent state / audio energy), and a waveform.
 */
@Composable
fun VoicePresence(agentState: AgentState, audioLevel: Float, modifier: Modifier = Modifier) {
    val energy = energyFor(agentState)
    val brightness by animateFloatAsState(energy.brightness, spring(stiffness = 50f), label = "brightness")
    val level by animateFloatAsState(audioLevel.coerceIn(0f, 1f), tween(120), label = "level")

    val pulsePeriod = (1900 - (energy.wobble * 900)).toInt().coerceAtLeast(700)
    val transition = rememberInfiniteTransition(label = "presence")
    val pulse by transition.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(pulsePeriod, easing = FastOutSlowInEasing)),
        label = "pulse",
    )
    val pulse2 by transition.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(pulsePeriod, easing = FastOutSlowInEasing), initialStartOffset = androidx.compose.animation.core.StartOffset((pulsePeriod * 0.5f).toInt())),
        label = "pulse2",
    )
    val breathe by transition.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(pulsePeriod * 2, easing = FastOutSlowInEasing), repeatMode = RepeatMode.Reverse),
        label = "breathe",
    )

    Box(modifier = modifier.size(268.dp), contentAlignment = Alignment.Center) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val center = Offset(size.width / 2f, size.height / 2f)
            val maxR = size.minDimension / 2f
            val discR = maxR * 0.46f * (1f + breathe * 0.015f * (0.3f + brightness))
            val lightSrc = center - Offset(discR * 0.38f, discR * 0.42f)

            // Ambient bloom — a violet-to-magenta neural gradient filling the
            // negative space around the disc, breathing with state brightness.
            drawCircle(
                brush = Brush.radialGradient(
                    colors = listOf(
                        SignatureB.copy(alpha = 0.20f + brightness * 0.22f),
                        SignatureA.copy(alpha = 0.10f + brightness * 0.14f),
                        Color.Transparent,
                    ),
                    center = center - Offset(discR * 0.2f, discR * 0.2f),
                    radius = maxR,
                ),
                radius = maxR,
                center = center,
            )

            // Two offset sonar rings, each sweeping from violet to magenta as
            // it expands, for a sense of continuous depth rather than a
            // single mechanical pulse.
            listOf(pulse, pulse2).forEach { p ->
                val ringR = discR + (maxR - discR) * p
                drawCircle(
                    color = lerp(SignatureA, SignatureB, p).copy(alpha = (1f - p) * (0.24f + brightness * 0.45f)),
                    radius = ringR,
                    center = center,
                    style = Stroke(width = 2.dp.toPx()),
                )
            }

            // Glass disc: dark radial gradient lit from one corner, with a
            // faint violet cast, giving it real dimensionality instead of a
            // flat fill.
            drawCircle(
                brush = Brush.radialGradient(
                    colors = listOf(
                        lerp(Panel, Color(0xFF352A52), 0.5f + brightness * 0.2f),
                        Panel,
                        Color(0xFF0E0C16),
                    ),
                    center = lightSrc,
                    radius = discR * 2.2f,
                ),
                radius = discR,
                center = center,
            )
            drawCircle(
                brush = Brush.linearGradient(
                    colors = listOf(SignatureA, SignatureB),
                    start = center - Offset(discR, discR),
                    end = center + Offset(discR, discR),
                ),
                alpha = 0.4f + brightness * 0.5f,
                radius = discR,
                center = center,
                style = Stroke(width = 1.5.dp.toPx()),
            )

            // Specular highlight — the detail that reads as "rendered," not
            // "flat vector circle."
            drawCircle(
                brush = Brush.radialGradient(
                    colors = listOf(Color.White.copy(alpha = 0.14f + brightness * 0.08f), Color.Transparent),
                    center = lightSrc,
                    radius = discR * 0.85f,
                ),
                radius = discR * 0.85f,
                center = lightSrc,
            )
        }

        Waveform(level = level, brightness = brightness, barCount = 5)
    }
}

/** A small vertical-bar waveform — the proven, low-risk way to show "this is
 * picking up real sound," reacting to actual audio level rather than a loop. */
@Composable
private fun Waveform(level: Float, brightness: Float, barCount: Int) {
    val transition = rememberInfiniteTransition(label = "wave")
    val phases = List(barCount) { i ->
        val phase by transition.animateFloat(
            0f, (2 * Math.PI).toFloat(),
            infiniteRepeatable(tween(620 + i * 90, easing = LinearEasing)),
            label = "phase$i",
        )
        phase
    }

    Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
        phases.forEachIndexed { i, phase ->
            val idleBob = (sin(phase) + 1f) / 2f // 0..1, out of phase per bar
            val energy = (level * 0.85f) + (idleBob * 0.15f * (0.4f + brightness))
            val targetHeight = (10 + energy * 46).dp
            val animatedHeight by animateFloatAsState(
                targetHeight.value,
                spring(dampingRatio = 0.55f, stiffness = 180f),
                label = "bar$i",
            )
            val barColor = lerp(SignatureA, SignatureB, i / (barCount - 1).toFloat().coerceAtLeast(1f))
            Box(
                modifier = Modifier
                    .width(5.dp)
                    .height(animatedHeight.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(lerp(SignatureDim, barColor, 0.35f + brightness * 0.65f)),
            )
        }
    }
}

private fun lerp(a: Color, b: Color, t: Float): Color {
    val ct = t.coerceIn(0f, 1f)
    return Color(
        red = a.red + (b.red - a.red) * ct,
        green = a.green + (b.green - a.green) * ct,
        blue = a.blue + (b.blue - a.blue) * ct,
        alpha = 1f,
    )
}

@Composable
fun InterviewScreen(
    authViewModel: com.techrifter.roundtableai.auth.AuthViewModel,
    modifier: Modifier = Modifier,
) {
    val application = LocalContext.current.applicationContext as android.app.Application
    val viewModel: InterviewViewModel = viewModel(
        factory = InterviewViewModel.Factory(application) { authViewModel.getIdToken() }
    )
    val uiState by viewModel.uiState.collectAsState()
    val agentState by viewModel.agentState.collectAsState()
    val transcript by viewModel.transcript.collectAsState()
    val signalStatus by viewModel.signalStatus.collectAsState()
    val localLevel by viewModel.localAudioLevel.collectAsState()
    val agentLevel by viewModel.agentAudioLevel.collectAsState()
    val currentPersona by viewModel.currentPersona.collectAsState()
    val report by viewModel.report.collectAsState()
    val profileState by viewModel.profileState.collectAsState()
    val userLimits by viewModel.userLimits.collectAsState()
    val isRefreshingLimits by viewModel.isRefreshingLimits.collectAsState()
    val selectedMode by viewModel.selectedMode.collectAsState()
    val selectedSkill by viewModel.selectedSkill.collectAsState()

    var showUpgradeDialog by remember { mutableStateOf(false) }
    var showAccountDialog by remember { mutableStateOf(false) }
    var showDeleteAccountDialog by remember { mutableStateOf(false) }
    var isDeletingAccount by remember { mutableStateOf(false) }
    var deleteAccountError by remember { mutableStateOf<String?>(null) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    if (showUpgradeDialog) {
        UpgradeProDialog(
            hasJoinedWaitlist = userLimits?.hasJoinedWaitlist == true,
            onDismiss = { showUpgradeDialog = false },
            onJoinWaitlist = { viewModel.joinWaitlist() },
        )
    }
    if (showAccountDialog) {
        Dialog(onDismissRequest = { showAccountDialog = false }) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(24.dp))
                    .background(Panel)
                    .border(1.dp, SignatureA.copy(alpha = 0.4f), RoundedCornerShape(24.dp))
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("Account", color = Ink, fontFamily = Inter, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(20.dp))
                Text(
                    "Privacy policy",
                    color = SignatureA,
                    fontFamily = Inter,
                    fontSize = 14.sp,
                    modifier = Modifier.clickable {
                        context.startActivity(
                            Intent(
                                Intent.ACTION_VIEW,
                                Uri.parse("https://sites.google.com/view/roundtableai-privacy-policy"),
                            )
                        )
                    },
                )
                Spacer(Modifier.height(18.dp))
                Text(
                    "Sign out",
                    color = Ink,
                    fontFamily = Inter,
                    fontSize = 14.sp,
                    modifier = Modifier.clickable {
                        showAccountDialog = false
                        authViewModel.signOut()
                    },
                )
                Spacer(Modifier.height(18.dp))
                Text(
                    "Delete account",
                    color = Danger,
                    fontFamily = Inter,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable {
                        showAccountDialog = false
                        deleteAccountError = null
                        showDeleteAccountDialog = true
                    },
                )
                Spacer(Modifier.height(20.dp))
                Text(
                    "Close",
                    color = InkMuted,
                    fontFamily = Inter,
                    fontSize = 13.sp,
                    modifier = Modifier.clickable { showAccountDialog = false },
                )
            }
        }
    }
    if (showDeleteAccountDialog) {
        Dialog(
            onDismissRequest = {
                if (!isDeletingAccount) showDeleteAccountDialog = false
            }
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(24.dp))
                    .background(Panel)
                    .border(1.dp, Danger.copy(alpha = 0.4f), RoundedCornerShape(24.dp))
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    "Request account deletion?",
                    color = Ink,
                    fontFamily = Inter,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    "Your request will be queued. Your account and associated data will be deleted within 48 hours. You will be signed out after the request is submitted.",
                    color = InkMuted,
                    fontFamily = Inter,
                    fontSize = 13.sp,
                    lineHeight = 19.sp,
                    textAlign = TextAlign.Center,
                )
                deleteAccountError?.let { message ->
                    Spacer(Modifier.height(12.dp))
                    Text(message, color = Danger, fontFamily = Inter, fontSize = 12.sp, textAlign = TextAlign.Center)
                }
                Spacer(Modifier.height(22.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "Cancel",
                        color = InkMuted,
                        fontFamily = Inter,
                        fontSize = 14.sp,
                        modifier = Modifier.weight(1f).clickable(enabled = !isDeletingAccount) {
                            showDeleteAccountDialog = false
                        }.padding(vertical = 12.dp),
                        textAlign = TextAlign.Center,
                    )
                    Text(
                        if (isDeletingAccount) "Submitting…" else "Request deletion",
                        color = Danger,
                        fontFamily = Inter,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.weight(1f).clickable(enabled = !isDeletingAccount) {
                            scope.launch {
                                isDeletingAccount = true
                                deleteAccountError = null
                                val result = authViewModel.deleteAccount()
                                isDeletingAccount = false
                                result.onSuccess {
                                    showDeleteAccountDialog = false
                                    android.widget.Toast.makeText(
                                        context,
                                        "Deletion requested. Your account and associated data will be deleted.",
                                        android.widget.Toast.LENGTH_LONG,
                                    ).show()
                                    authViewModel.signOut()
                                }.onFailure {
                                    deleteAccountError = it.message ?: "Could not submit the deletion request. Please try again."
                                }
                            }
                        }.padding(vertical = 12.dp),
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Brush.radialGradient(colors = listOf(Color(0xFF241C3A), Backdrop)))
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        when (uiState.status) {
            InterviewStatus.LIVE -> LiveInterviewContent(
                agentState = agentState,
                transcript = transcript,
                signalStatus = signalStatus,
                localLevel = localLevel,
                agentLevel = agentLevel,
                persona = currentPersona,
                selectedMode = selectedMode,
                selectedSkill = selectedSkill,
                onEnd = { viewModel.endInterview() },
            )
            InterviewStatus.REPORT -> ReportContent(
                reportState = report,
                selectedSkill = selectedSkill,
                onRestart = { viewModel.startNewInterview() },
                onRetryDrill = { skill -> viewModel.retryCurrentDrill(skill) },
            )
            else -> PreJoinContent(
                uiState = uiState,
                profileState = profileState,
                userLimits = userLimits,
                selectedMode = selectedMode,
                selectedSkill = selectedSkill,
                onSelectMode = { viewModel.selectMode(it) },
                onSelectSkill = { viewModel.selectSkill(it) },
                onRefreshLimits = { viewModel.refreshUserLimits() },
                isRefreshingLimits = isRefreshingLimits,
                onUpgradeClick = { showUpgradeDialog = true },
                onAnalyze = { jd, resume -> viewModel.analyzeProfile(jd, resume) },
                onChangeDocuments = { viewModel.resetProfile() },
                onStart = { viewModel.startInterview() },
            )
        }
        if (uiState.status != InterviewStatus.LIVE) {
            Text(
                text = "Account",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .clickable { showAccountDialog = true }
                    .padding(horizontal = 12.dp, vertical = 8.dp),
            )
        }
    }
}

private val DocumentMimeTypes = arrayOf(
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

@Composable
private fun PreJoinContent(
    uiState: InterviewUiState,
    profileState: ProfileUiState,
    userLimits: UserLimits?,
    selectedMode: InterviewMode,
    selectedSkill: String?,
    onSelectMode: (InterviewMode) -> Unit,
    onSelectSkill: (String) -> Unit,
    onRefreshLimits: () -> Unit,
    isRefreshingLimits: Boolean,
    onUpgradeClick: () -> Unit,
    onAnalyze: (Uri?, Uri?) -> Unit,
    onChangeDocuments: () -> Unit,
    onStart: () -> Unit,
) {
    val context = LocalContext.current
    var hasMicPermission by remember { mutableStateOf(false) }
    var jdUri by remember { mutableStateOf<Uri?>(null) }
    var resumeUri by remember { mutableStateOf<Uri?>(null) }

    // Request RECORD_AUDIO + BLUETOOTH_CONNECT (API 31+ only) together in one dialog.
    // BLUETOOTH_CONNECT denial is non-fatal: audio falls back to earpiece/speaker.
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        hasMicPermission = grants[Manifest.permission.RECORD_AUDIO] == true
        if (hasMicPermission) onStart()
    }

    LaunchedEffect(Unit) {
        hasMicPermission = ContextCompat.checkSelfPermission(
            context, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }

    val jdPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) jdUri = uri
    }
    val resumePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) resumeUri = uri
    }

    val isBusy = uiState.status == InterviewStatus.REQUESTING_SESSION || uiState.status == InterviewStatus.JOINING

    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 32.dp, vertical = 28.dp)) {
        Column(
            modifier = Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // Plan / Limit Status Pill
            val isLimitReached = userLimits?.canStart == false
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(20.dp))
                    .background(if (isLimitReached) Danger.copy(alpha = 0.15f) else SignatureDim)
                    .border(
                        1.dp,
                        if (isLimitReached) Danger.copy(alpha = 0.45f) else SignatureA.copy(alpha = 0.35f),
                        RoundedCornerShape(20.dp),
                    )
                    .clickable(enabled = userLimits != null || !isRefreshingLimits) {
                        if (userLimits == null) onRefreshLimits() else onUpgradeClick()
                    }
                    .padding(horizontal = 14.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = when {
                        userLimits == null && isRefreshingLimits -> "Checking limit status…"
                        userLimits == null -> "Limit status unavailable • Tap to retry"
                        userLimits.hasJoinedWaitlist && isLimitReached ->
                            "Daily limit reached (0/${userLimits.dailyLimit}) • Priority Waitlist"
                        userLimits.hasJoinedWaitlist ->
                            "Daily limit: ${userLimits.remaining}/${userLimits.dailyLimit} • Priority Waitlist"
                        isLimitReached -> "Daily limit reached (0/${userLimits.dailyLimit}) • Upgrade"
                        else ->
                            "Daily limit: ${userLimits.remaining}/${userLimits.dailyLimit} • Upgrade"
                    },
                    color = if (isLimitReached) Danger else Ink,
                    fontFamily = Inter,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.3.sp,
                )
            }

            Spacer(Modifier.height(18.dp))

            Text(
                text = "LIVE VOICE INTERVIEW PANEL",
                color = Signature,
                fontFamily = Inter,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 2.4.sp,
            )
            Spacer(Modifier.height(20.dp))
            VoicePresence(agentState = AgentState.IDLE, audioLevel = 0f, modifier = Modifier.size(170.dp))
            Spacer(Modifier.height(28.dp))
            Text(
                text = "Roundtable AI",
                color = Ink,
                fontSize = 36.sp,
                fontFamily = Inter,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1.2).sp,
            )
            Spacer(Modifier.height(24.dp))

            if (profileState.status == ProfileStatus.READY && profileState.profile != null) {
                ProfileReadySection(
                    profile = profileState.profile,
                    selectedMode = selectedMode,
                    selectedSkill = selectedSkill,
                    onSelectMode = onSelectMode,
                    onSelectSkill = onSelectSkill,
                    isBusy = isBusy,
                    errorMessage = uiState.errorMessage,
                    userLimits = userLimits,
                    onUpgradeClick = onUpgradeClick,
                    onChangeDocuments = onChangeDocuments,
                    onStart = {
                        if (userLimits?.canStart == false) {
                            onUpgradeClick()
                        } else if (hasMicPermission) {
                            onStart()
                        } else {
                            val perms = buildList {
                                add(Manifest.permission.RECORD_AUDIO)
                                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                                    add(Manifest.permission.BLUETOOTH_CONNECT)
                                }
                            }
                            permissionLauncher.launch(perms.toTypedArray())
                        }
                    },
                )
            } else {
                DocumentSetupSection(
                    profileState = profileState,
                    jdName = jdUri?.let { resolveDisplayName(context, it) },
                    resumeName = resumeUri?.let { resolveDisplayName(context, it) },
                    userLimits = userLimits,
                    onUpgradeClick = onUpgradeClick,
                    onPickJd = { jdPicker.launch(DocumentMimeTypes) },
                    onPickResume = { resumePicker.launch(DocumentMimeTypes) },
                    onAnalyze = { onAnalyze(jdUri, resumeUri) },
                )
            }
        }
    }
}

@Composable
private fun DocumentSetupSection(
    profileState: ProfileUiState,
    jdName: String?,
    resumeName: String?,
    userLimits: UserLimits?,
    onUpgradeClick: () -> Unit,
    onPickJd: () -> Unit,
    onPickResume: () -> Unit,
    onAnalyze: () -> Unit,
) {
    val isUploading = profileState.status == ProfileStatus.UPLOADING
    val isLimitReached = userLimits?.canStart == false
    val canAnalyze = (jdName != null || resumeName != null) && !isUploading

    Text(
        text = "Share the role you're applying for or your resume. The panel will build the interview around you.",
        color = InkMuted,
        fontFamily = Inter,
        fontSize = 14.sp,
        textAlign = TextAlign.Center,
        lineHeight = 20.sp,
    )
    Spacer(Modifier.height(24.dp))

    DocumentPickerRow(label = "Job description", fileName = jdName, enabled = !isUploading, onPick = onPickJd)
    Spacer(Modifier.height(12.dp))
    DocumentPickerRow(label = "Your resume", fileName = resumeName, enabled = !isUploading, onPick = onPickResume)

    if (isLimitReached) {
        Spacer(Modifier.height(16.dp))
        Text(
            text = "Upgrade to Pro for unlimited practice →",
            color = SignatureA,
            fontFamily = Inter,
            fontSize = 13.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.clickable { onUpgradeClick() }
        )
    } else if (profileState.status == ProfileStatus.FAILED) {
        Spacer(Modifier.height(16.dp))
        Text(
            text = profileState.errorMessage ?: "Couldn't read that JD/resume.",
            color = Danger,
            fontFamily = Inter,
            fontSize = 13.sp,
            textAlign = TextAlign.Center,
        )
    }

    Spacer(Modifier.height(28.dp))
    PrimaryButton(
        label = if (isLimitReached) "Upgrade to Pro" else if (isUploading) "Reading documents…" else "Analyze documents",
        enabled = if (isLimitReached) true else canAnalyze,
        onClick = if (isLimitReached) onUpgradeClick else onAnalyze,
    )
}

@Composable
private fun DocumentPickerRow(label: String, fileName: String?, enabled: Boolean, onPick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Panel.copy(alpha = 0.6f))
            .clickable(
                enabled = enabled,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onPick,
            )
            .padding(horizontal = 18.dp, vertical = 16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column {
            Text(text = label, color = InkFaint, fontFamily = Inter, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp)
            Spacer(Modifier.height(3.dp))
            Text(
                text = fileName ?: "Choose a PDF or DOCX file",
                color = if (fileName != null) Ink else InkMuted,
                fontFamily = Inter,
                fontSize = 14.sp,
                fontWeight = if (fileName != null) FontWeight.Medium else FontWeight.Normal,
            )
        }
        Text(text = if (fileName != null) "Change" else "Browse", color = Signature, fontFamily = Inter, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun ProfileReadySection(
    profile: InterviewProfilePreview,
    selectedMode: InterviewMode,
    selectedSkill: String?,
    onSelectMode: (InterviewMode) -> Unit,
    onSelectSkill: (String) -> Unit,
    isBusy: Boolean,
    errorMessage: String?,
    userLimits: UserLimits?,
    onUpgradeClick: () -> Unit,
    onChangeDocuments: () -> Unit,
    onStart: () -> Unit,
) {
    val isLimitReached = userLimits?.canStart == false && !isBusy

    Text(
        text = "READY TO INTERVIEW FOR",
        color = InkFaint,
        fontFamily = Inter,
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.4.sp,
    )
    Spacer(Modifier.height(6.dp))
    Text(
        text = profile.role,
        color = Ink,
        fontFamily = Inter,
        fontSize = 22.sp,
        fontWeight = FontWeight.Bold,
        textAlign = TextAlign.Center,
    )
    Spacer(Modifier.height(20.dp))

    Text(
        text = "CHOOSE INTERVIEW MODE",
        color = InkFaint,
        fontFamily = Inter,
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.2.sp,
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(10.dp))

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        // Option 1: Focused Practice
        ModeOptionCard(
            title = "Focused Practice",
            duration = "5–15 min",
            badgeText = "⚡ POPULAR",
            badgeColor = SignatureB,
            description = "Pick one skill, practise targeted questions, and receive instant coaching feedback with rapid retries.",
            isSelected = selectedMode == InterviewMode.FOCUSED,
            onClick = { onSelectMode(InterviewMode.FOCUSED) },
        ) {
            if (profile.skills.isNotEmpty()) {
                Text(
                    text = "SELECT TARGET SKILL",
                    color = InkFaint,
                    fontFamily = Inter,
                    fontSize = 10.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 1.sp,
                    modifier = Modifier.padding(start = 28.dp, bottom = 6.dp),
                )
                Box(modifier = Modifier.padding(start = 28.dp)) {
                    SelectableSkillChipsRow(
                        skills = profile.skills.take(8),
                        selectedSkill = selectedSkill,
                        onSelectSkill = onSelectSkill,
                    )
                }
            }
        }

        // Option 2: Full Interview Simulation
        ModeOptionCard(
            title = "Full Simulation",
            duration = "30–40 min",
            badgeText = "🧭 STANDARD",
            badgeColor = SignatureA,
            description = "Three interviewers, multi-stage adaptive questioning, and a comprehensive rubric evaluation.",
            isSelected = selectedMode == InterviewMode.FULL,
            onClick = { onSelectMode(InterviewMode.FULL) },
        ) {
            if (profile.skills.isNotEmpty()) {
                Box(modifier = Modifier.padding(start = 28.dp)) {
                    SkillChipsRow(skills = profile.skills.take(5))
                }
            }
        }
    }

    if (errorMessage != null && !isBusy) {
        Spacer(Modifier.height(16.dp))
        Text(text = errorMessage, color = Danger, fontFamily = Inter, fontSize = 13.sp, textAlign = TextAlign.Center)
    } else if (isLimitReached) {
        Spacer(Modifier.height(16.dp))
        Text(
            text = "Upgrade to Pro for unlimited practice →",
            color = SignatureA,
            fontFamily = Inter,
            fontSize = 13.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.clickable { onUpgradeClick() }
        )
    }

    Spacer(Modifier.height(26.dp))
    val buttonLabel = when {
        isBusy -> "Connecting…"
        isLimitReached -> "Upgrade to Pro"
        selectedMode == InterviewMode.FOCUSED -> "Begin Focused Practice"
        else -> "Begin Full Interview"
    }
    PrimaryButton(
        label = buttonLabel,
        enabled = !isBusy,
        onClick = if (isLimitReached) onUpgradeClick else onStart,
    )
    Spacer(Modifier.height(14.dp))
    Text(
        text = "Change documents",
        color = if (isBusy) InkFaint.copy(alpha = 0.5f) else InkFaint,
        fontFamily = Inter,
        fontSize = 13.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.clickable(
            enabled = !isBusy,
            indication = null,
            interactionSource = remember { MutableInteractionSource() },
            onClick = onChangeDocuments,
        ),
    )
}

@Composable
private fun ModeOptionCard(
    title: String,
    duration: String,
    badgeText: String,
    badgeColor: Color,
    description: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    content: @Composable (() -> Unit)? = null,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(if (isSelected) Panel else Panel.copy(alpha = 0.5f))
            .border(
                width = if (isSelected) 1.5.dp else 1.dp,
                brush = if (isSelected) Brush.horizontalGradient(listOf(SignatureA, SignatureB))
                else Brush.linearGradient(listOf(SignatureDim, SignatureDim)),
                shape = RoundedCornerShape(18.dp),
            )
            .clickable(
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onClick,
            )
            .padding(14.dp),
    ) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(18.dp)
                            .clip(CircleShape)
                            .border(1.5.dp, if (isSelected) SignatureB else InkFaint.copy(alpha = 0.4f), CircleShape)
                            .padding(3.dp),
                    ) {
                        if (isSelected) {
                            Box(
                                modifier = Modifier
                                    .fillMaxSize()
                                    .clip(CircleShape)
                                    .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB))),
                            )
                        }
                    }
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = title,
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 14.5.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = duration,
                        color = InkFaint,
                        fontFamily = Inter,
                        fontSize = 11.5.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(badgeColor.copy(alpha = 0.15f))
                            .border(1.dp, badgeColor.copy(alpha = 0.4f), RoundedCornerShape(8.dp))
                            .padding(horizontal = 7.dp, vertical = 2.dp),
                    ) {
                        Text(
                            text = badgeText,
                            color = badgeColor,
                            fontFamily = Inter,
                            fontSize = 9.5.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 0.6.sp,
                        )
                    }
                }
            }

            Spacer(Modifier.height(5.dp))
            Text(
                text = description,
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 12.sp,
                lineHeight = 16.5.sp,
                modifier = Modifier.padding(start = 28.dp),
            )

            if (content != null) {
                Spacer(Modifier.height(10.dp))
                content()
            }
        }
    }
}

@Composable
private fun SelectableSkillChipsRow(
    skills: List<String>,
    selectedSkill: String?,
    onSelectSkill: (String) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        skills.forEach { skill ->
            val isSelected = skill == selectedSkill
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(12.dp))
                    .background(if (isSelected) SignatureDim else Panel)
                    .border(
                        width = 1.dp,
                        color = if (isSelected) SignatureA else SignatureDim,
                        shape = RoundedCornerShape(12.dp),
                    )
                    .clickable { onSelectSkill(skill) }
                    .padding(horizontal = 11.dp, vertical = 6.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (isSelected) {
                        Text(text = "✓ ", color = SignatureA, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                    }
                    Text(
                        text = skill,
                        color = if (isSelected) Ink else InkMuted,
                        fontFamily = Inter,
                        fontSize = 12.sp,
                        fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal,
                    )
                }
            }
        }
    }
}

@Composable
private fun SkillChipsRow(skills: List<String>) {
    // A plain Row (not a wrapping layout) keeps this dependency-free; six short
    // skill words fit one line at this width without needing FlowRow.
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        skills.forEach { skill ->
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(12.dp))
                    .background(SignatureDim)
                    .padding(horizontal = 12.dp, vertical = 7.dp),
            ) {
                Text(text = skill, color = Ink, fontFamily = Inter, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}

@Composable
private fun PrimaryButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .height(56.dp)
            .widthIn(min = 232.dp, max = 290.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(if (enabled) Ink else Ink.copy(alpha = 0.25f))
            .clickable(
                enabled = enabled,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onClick,
            )
            .padding(horizontal = 24.dp),
    ) {
        Text(text = label, color = Backdrop, fontFamily = Inter, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun LiveInterviewContent(
    agentState: AgentState,
    transcript: List<TranscriptEntry>,
    signalStatus: SignalStatus,
    localLevel: Float,
    agentLevel: Float,
    persona: String?,
    selectedMode: InterviewMode = InterviewMode.FOCUSED,
    selectedSkill: String? = null,
    onEnd: () -> Unit,
) {
    var elapsedSeconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            elapsedSeconds++
        }
    }

    val activeLevel = if (agentState == AgentState.SPEAKING) agentLevel else localLevel

    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 28.dp).padding(top = 20.dp, bottom = 24.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                PulsingDot(color = Danger)
                Spacer(Modifier.width(8.dp))
                Text(text = formatElapsed(elapsedSeconds), color = InkFaint, fontFamily = InterTabular, fontSize = 13.sp)
            }
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .clickable(
                        indication = null,
                        interactionSource = remember { MutableInteractionSource() },
                        onClick = onEnd,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "End",
                    color = Danger.copy(alpha = 0.85f),
                    fontFamily = Inter,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                )
            }
        }

        if (selectedMode == InterviewMode.FOCUSED) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 18.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Panel)
                    .border(1.dp, SignatureA.copy(alpha = 0.35f), RoundedCornerShape(12.dp))
                    .padding(horizontal = 14.dp, vertical = 7.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "FOCUSED PRACTICE • ${(selectedSkill ?: "TARGET SKILL").uppercase()}",
                    color = SignatureB,
                    fontFamily = Inter,
                    fontSize = 11.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 1.2.sp,
                )
            }
        } else {
            StageProgress(currentStage = persona, modifier = Modifier.fillMaxWidth().padding(top = 18.dp))
        }

        Column(
            modifier = Modifier.fillMaxWidth().padding(top = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            AnimatedContent(targetState = personaLabelFor(persona), label = "persona") { current ->
                Text(
                    text = current.uppercase(),
                    color = InkFaint,
                    fontFamily = Inter,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    letterSpacing = 1.6.sp,
                )
            }
            Spacer(Modifier.height(28.dp))
            VoicePresence(agentState = agentState, audioLevel = activeLevel)
            Spacer(Modifier.height(28.dp))
            Text(
                text = statusLineFor(agentState),
                color = Ink,
                fontFamily = Inter,
                fontSize = 23.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = (-0.4).sp,
            )
            if (signalStatus != SignalStatus.CONNECTED) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = if (signalStatus == SignalStatus.FAILED) "Live captions unavailable" else "Connecting captions…",
                    color = InkFaint,
                    fontFamily = Inter,
                    fontSize = 12.sp,
                )
            }
        }

        Spacer(Modifier.height(24.dp))
        TranscriptPanel(transcript = transcript, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun PulsingDot(color: Color) {
    val transition = rememberInfiniteTransition(label = "dot")
    val alpha by transition.animateFloat(
        1f, 0.35f,
        infiniteRepeatable(tween(1000, easing = FastOutSlowInEasing), repeatMode = RepeatMode.Reverse),
        label = "dot-alpha",
    )
    Box(modifier = Modifier.size(7.dp).clip(CircleShape).background(color.copy(alpha = alpha)))
}

/** Consecutive same-speaker turns merged into one block. Agora issues a new turnId
 * whenever ASR finalizes on a pause, not just on speaker changes, so unmerged
 * entries render one stretch of speech as several back-to-back bubbles. */
private data class TranscriptBlock(val key: String, val speaker: Speaker, val text: String, val isFinal: Boolean)

private fun groupTranscript(entries: List<TranscriptEntry>): List<TranscriptBlock> {
    val blocks = mutableListOf<TranscriptBlock>()
    for (entry in entries) {
        val last = blocks.lastOrNull()
        if (last != null && last.speaker == entry.speaker) {
            blocks[blocks.lastIndex] = last.copy(
                key = "${last.key}+${entry.turnId}",
                text = "${last.text} ${entry.text}".trim(),
                isFinal = entry.isFinal,
            )
        } else {
            blocks.add(
                TranscriptBlock(
                    key = "${entry.turnId}-${entry.speaker}",
                    speaker = entry.speaker,
                    text = entry.text,
                    isFinal = entry.isFinal,
                )
            )
        }
    }
    return blocks
}

@Composable
private fun TranscriptPanel(transcript: List<TranscriptEntry>, modifier: Modifier = Modifier) {
    val blocks = remember(transcript) { groupTranscript(transcript) }
    val listState = rememberLazyListState()
    LaunchedEffect(blocks.size, blocks.lastOrNull()?.text) {
        if (blocks.isNotEmpty()) listState.animateScrollToItem(blocks.size - 1)
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(24.dp))
            .background(Panel.copy(alpha = 0.6f)),
    ) {
        if (blocks.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize().padding(20.dp), contentAlignment = Alignment.Center) {
                Text(text = "Waiting for the conversation to begin…", color = InkFaint, fontFamily = Inter, fontSize = 13.sp)
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(horizontal = 22.dp, vertical = 20.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                itemsIndexed(blocks, key = { _, block -> block.key }) { index, block ->
                    val isLatest = index == blocks.lastIndex
                    Column {
                        SpeakerLabel(speaker = block.speaker)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = block.text + if (!block.isFinal && isLatest) " ●" else "",
                            color = if (isLatest && !block.isFinal) Ink else InkMuted,
                            fontFamily = Inter,
                            fontSize = 15.sp,
                            lineHeight = 22.sp,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SpeakerLabel(speaker: Speaker) {
    val (label, color) = if (speaker == Speaker.AGENT) "PANEL" to Signature else "YOU" to InkFaint
    Text(text = label, color = color, fontFamily = Inter, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp)
}

@Composable
private fun ReportContent(
    reportState: ReportUiState,
    selectedSkill: String?,
    onRestart: () -> Unit,
    onRetryDrill: (String?) -> Unit,
) {
    when (reportState.status) {
        "ready" -> reportState.report?.let {
            ReportReadyContent(
                report = it,
                selectedSkill = selectedSkill,
                onRestart = onRestart,
                onRetryDrill = onRetryDrill,
            )
        } ?: ReportFailedContent(onRestart = onRestart)
        "failed" -> ReportFailedContent(onRestart = onRestart)
        "not_applicable" -> ReportEndedEarlyContent(onRestart = onRestart)
        else -> ReportPendingContent()
    }
}

@Composable
private fun ReportEndedEarlyContent(onRestart: () -> Unit) {
    Box(modifier = Modifier.fillMaxSize().padding(36.dp)) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "Nothing to score yet",
                color = Ink,
                fontFamily = Inter,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                text = "The call ended before you answered anything, so there's no feedback to give this time.",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(36.dp))
            PrimaryButton(label = "Start new interview", enabled = true, onClick = onRestart)
        }
    }
}

@Composable
private fun ReportPendingContent() {
    Box(modifier = Modifier.fillMaxSize().padding(36.dp)) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            VoicePresence(agentState = AgentState.THINKING, audioLevel = 0f)
            Spacer(Modifier.height(40.dp))
            Text(
                text = "Scoring your interview",
                color = Ink,
                fontFamily = Inter,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = (-0.4).sp,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                text = "The panel is putting together your feedback, this takes a few seconds.",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun ReportFailedContent(onRestart: () -> Unit) {
    Box(modifier = Modifier.fillMaxSize().padding(36.dp)) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "We couldn't generate your feedback",
                color = Ink,
                fontFamily = Inter,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                text = "Something went wrong scoring this interview. Your conversation itself wasn't affected.",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(36.dp))
            PrimaryButton(label = "Start new interview", enabled = true, onClick = onRestart)
        }
    }
}

/** The panel's feedback report, once it's ready. Shows the overall score, per-category
 * scores, and the strengths / areas to improve with evidence from the conversation.
 */
@Composable
private fun ReportReadyContent(
    report: InterviewReport,
    selectedSkill: String?,
    onRestart: () -> Unit,
    onRetryDrill: (String?) -> Unit,
) {
    val isFocused = report.mode == "focused"
    val drillSkill = report.targetSkill ?: selectedSkill

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 28.dp)
            .padding(top = 28.dp, bottom = 36.dp),
    ) {
        Text(
            text = if (isFocused) "FOCUSED DRILL FEEDBACK" else "YOUR PANEL FEEDBACK",
            color = Signature,
            fontFamily = Inter,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 2.2.sp,
        )
        if (isFocused && !drillSkill.isNullOrBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = "TARGET SKILL: ${drillSkill.uppercase()}",
                color = SignatureB,
                fontFamily = Inter,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.2.sp,
            )
        }
        Spacer(Modifier.height(18.dp))

        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                text = formatScore(report.overall),
                color = Ink,
                fontFamily = Inter,
                fontSize = 56.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1.4).sp,
            )
            Text(
                text = " / 10 overall",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 16.sp,
                modifier = Modifier.padding(bottom = 10.dp),
            )
        }
        Spacer(Modifier.height(14.dp))
        Text(text = report.summary, color = InkMuted, fontFamily = Inter, fontSize = 15.sp, lineHeight = 22.sp)

        Spacer(Modifier.height(32.dp))
        if (isFocused) {
            ScoreRow(drillSkill ?: "Skill Accuracy", report.technical)
            Spacer(Modifier.height(14.dp))
            ScoreRow("Communication & Clarity", report.communication)
        } else {
            ScoreRow("Technical", report.technical)
            Spacer(Modifier.height(14.dp))
            ScoreRow("System Design", report.systemDesign)
            Spacer(Modifier.height(14.dp))
            ScoreRow("Behavioral", report.behavioral)
            Spacer(Modifier.height(14.dp))
            ScoreRow("Communication", report.communication)
        }

        Spacer(Modifier.height(36.dp))
        EvidenceSection(title = "Strengths", points = report.strengths, accent = SignatureA)
        Spacer(Modifier.height(28.dp))
        EvidenceSection(title = if (isFocused) "Targeted Drill Takeaways" else "Areas to improve", points = report.improvements, accent = SignatureB)

        Spacer(Modifier.height(40.dp))
        if (isFocused) {
            PrimaryButton(
                label = "Retry this drill",
                enabled = true,
                onClick = { onRetryDrill(drillSkill) },
            )
            Spacer(Modifier.height(14.dp))
            Text(
                text = "Practice another skill or full interview",
                color = InkFaint,
                fontFamily = Inter,
                fontSize = 13.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.clickable { onRestart() },
            )
        } else {
            PrimaryButton(label = "Start new interview", enabled = true, onClick = onRestart)
        }
    }
}

@Composable
private fun ScoreRow(label: String, score: Double) {
    Column {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(text = label, color = Ink, fontFamily = Inter, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(text = formatScore(score), color = InkMuted, fontFamily = InterTabular, fontSize = 14.sp)
        }
        Spacer(Modifier.height(8.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp))
                .background(SignatureDim),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth((score / 10.0).toFloat().coerceIn(0f, 1f))
                    .height(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB))),
            )
        }
    }
}

@Composable
private fun EvidenceSection(title: String, points: List<EvidencePoint>, accent: Color) {
    if (points.isEmpty()) return
    Column {
        Text(
            text = title.uppercase(),
            color = InkFaint,
            fontFamily = Inter,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 1.4.sp,
        )
        Spacer(Modifier.height(14.dp))
        Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
            points.forEach { EvidenceCard(point = it, accent = accent) }
        }
    }
}

@Composable
private fun EvidenceCard(point: EvidencePoint, accent: Color) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Panel.copy(alpha = 0.6f))
            .padding(16.dp),
    ) {
        Box(
            modifier = Modifier
                .padding(top = 5.dp)
                .size(7.dp)
                .clip(CircleShape)
                .background(accent),
        )
        Spacer(Modifier.width(12.dp))
        Column {
            Text(text = point.point, color = Ink, fontFamily = Inter, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Text(text = point.evidence, color = InkMuted, fontFamily = Inter, fontSize = 13.sp, lineHeight = 19.sp)
        }
    }
}

private fun formatScore(score: Double): String = "%.1f".format(score)

private fun formatElapsed(totalSeconds: Int): String {
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%02d:%02d".format(minutes, seconds)
}

@Composable
private fun UpgradeProDialog(
    hasJoinedWaitlist: Boolean = false,
    onDismiss: () -> Unit,
    onJoinWaitlist: suspend () -> Boolean,
) {
    var joinedWaitlist by remember(hasJoinedWaitlist) { mutableStateOf(hasJoinedWaitlist) }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Dialog(onDismissRequest = onDismiss) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(24.dp))
                .background(Panel)
                .border(1.dp, SignatureA.copy(alpha = 0.4f), RoundedCornerShape(24.dp))
                .padding(24.dp)
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(12.dp))
                        .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB)))
                        .padding(horizontal = 14.dp, vertical = 6.dp)
                ) {
                    Text(
                        text = "ROUNDTABLE PRO",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 1.6.sp
                    )
                }
                Spacer(Modifier.height(16.dp))
                Text(
                    text = "Daily Practice Limit",
                    color = Ink,
                    fontFamily = Inter,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Free accounts get 2 interviews in a rolling 24-hour window. Upgrade to Pro for unlimited simulations, targeted skill drills, and advanced panel customization.",
                    color = InkMuted,
                    fontFamily = Inter,
                    fontSize = 13.5.sp,
                    lineHeight = 19.sp,
                    textAlign = TextAlign.Center
                )
                Spacer(Modifier.height(20.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(16.dp))
                        .background(Backdrop)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    ProFeatureRow("⚡", "Unlimited AI voice interviews daily")
                    ProFeatureRow("🎯", "In-depth turn analysis & rubric feedback")
                    ProFeatureRow("🏢", "Custom FAANG & startup panel difficulty")
                    ProFeatureRow("🔄", "Targeted technical & system design practice")
                }
                Spacer(Modifier.height(24.dp))
                if (joinedWaitlist) {
                    Text(
                        text = "🎉 You're on the priority waitlist! We'll notify you as soon as Pro billing is available.",
                        color = SignatureB,
                        fontFamily = Inter,
                        fontSize = 13.sp,
                        textAlign = TextAlign.Center,
                        fontWeight = FontWeight.Medium,
                    )
                } else {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp)
                            .clip(RoundedCornerShape(24.dp))
                            .background(
                                if (isSubmitting) Brush.horizontalGradient(listOf(SignatureA.copy(alpha = 0.5f), SignatureB.copy(alpha = 0.5f)))
                                else Brush.horizontalGradient(listOf(SignatureA, SignatureB))
                            )
                            .clickable(enabled = !isSubmitting) {
                                scope.launch {
                                    isSubmitting = true
                                    errorMessage = null
                                    val success = onJoinWaitlist()
                                    isSubmitting = false
                                    if (success) {
                                        joinedWaitlist = true
                                    } else {
                                        errorMessage = "Couldn't reach server. Please check your connection."
                                    }
                                }
                            },
                        contentAlignment = Alignment.Center
                    ) {
                        if (isSubmitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(22.dp),
                                color = Ink,
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Text(
                                text = "Join Pro Waitlist (50% Off)",
                                color = Ink,
                                fontFamily = Inter,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
                if (errorMessage != null) {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = errorMessage!!,
                        color = Danger,
                        fontFamily = Inter,
                        fontSize = 12.5.sp,
                        textAlign = TextAlign.Center,
                    )
                }
                Spacer(Modifier.height(14.dp))
                Text(
                    text = if (joinedWaitlist) "Close" else "Maybe later",
                    color = InkFaint,
                    fontFamily = Inter,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.clickable { onDismiss() }
                )
            }
        }
    }
}

@Composable
private fun ProFeatureRow(icon: String, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text = icon, fontSize = 15.sp)
        Spacer(Modifier.width(10.dp))
        Text(text = text, color = Ink, fontFamily = Inter, fontSize = 13.sp, fontWeight = FontWeight.Normal)
    }
}
