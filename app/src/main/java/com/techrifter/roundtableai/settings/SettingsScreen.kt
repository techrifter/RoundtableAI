package com.techrifter.roundtableai.settings

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.techrifter.roundtableai.R

private val Backdrop = Color(0xFF0A0A12)
private val Panel = Color(0xFF181622)
private val Ink = Color(0xFFF6F4FF)
private val InkMuted = Color(0xFFACA6C4)
private val InkFaint = Color(0xFF625D7A)
private val SignatureA = Color(0xFF8B5CF6)
private val SignatureB = Color(0xFFFF3DA6)
private val SignatureDim = Color(0xFF2B2044)
private val Danger = Color(0xFFFF4D6D)
private val SuccessColor = Color(0xFF10B981)

@OptIn(ExperimentalTextApi::class)
private val Inter = FontFamily(
    Font(R.font.inter_variable, weight = FontWeight.Normal, variationSettings = FontVariation.Settings(FontVariation.weight(400))),
    Font(R.font.inter_variable, weight = FontWeight.Medium, variationSettings = FontVariation.Settings(FontVariation.weight(500))),
    Font(R.font.inter_variable, weight = FontWeight.SemiBold, variationSettings = FontVariation.Settings(FontVariation.weight(600))),
    Font(R.font.inter_variable, weight = FontWeight.Bold, variationSettings = FontVariation.Settings(FontVariation.weight(700))),
    Font(R.font.inter_variable, weight = FontWeight.ExtraBold, variationSettings = FontVariation.Settings(FontVariation.weight(800))),
)

private const val AGORA_PROJECTS_URL = "https://console.agora.io/projects"
private const val AGORA_RESTFUL_URL = "https://console.agora.io/legacy/restful-api"
private const val GROQ_KEYS_URL = "https://console.groq.com/keys"

@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onBack: () -> Unit
) {
    BackHandler(onBack = onBack)

    val uiState by viewModel.uiState.collectAsState()
    val uriHandler = LocalUriHandler.current

    var appId by remember { mutableStateOf("") }
    var appCert by remember { mutableStateOf("") }
    var customerId by remember { mutableStateOf("") }
    var customerSecret by remember { mutableStateOf("") }
    var groqApiKey by remember { mutableStateOf("") }

    var certVisible by remember { mutableStateOf(false) }
    var secretVisible by remember { mutableStateOf(false) }
    var groqVisible by remember { mutableStateOf(false) }
    var showGuide by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        viewModel.fetchStatus()
    }

    LaunchedEffect(uiState) {
        val state = uiState
        if (state is SettingsUiState.Success && state.credentials != null) {
            val creds = state.credentials
            appId = creds.appId
            appCert = creds.appCertificate
            customerId = creds.customerId
            customerSecret = creds.customerSecret
            groqApiKey = creds.groqApiKey
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Backdrop)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding()
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp, vertical = 16.dp)
                .verticalScroll(rememberScrollState())
        ) {
            // Header Bar
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(20.dp))
                        .background(Panel)
                        .border(1.dp, InkFaint.copy(alpha = 0.25f), RoundedCornerShape(20.dp))
                        .clickable { onBack() }
                        .padding(horizontal = 16.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = "← Back",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(12.dp))
                        .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB)))
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = "BYOK CONFIGURATION",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.ExtraBold,
                        letterSpacing = 1.2.sp
                    )
                }
            }

            Spacer(Modifier.height(24.dp))

            Text(
                text = "BYOK Setup",
                color = Ink,
                fontFamily = Inter,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "Connect your personal Agora and Groq keys to enjoy unlimited, unthrottled mock interview practice.",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 13.5.sp,
                lineHeight = 19.sp
            )

            Spacer(Modifier.height(20.dp))

            // Status Card
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(20.dp))
                    .background(Panel)
                    .border(1.dp, SignatureDim, RoundedCornerShape(20.dp))
                    .padding(18.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "CONNECTION STATUS",
                            color = InkFaint,
                            fontFamily = Inter,
                            fontSize = 10.5.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 1.2.sp
                        )
                        Spacer(Modifier.height(4.dp))
                        when (val state = uiState) {
                            is SettingsUiState.Loading -> {
                                Text(
                                    text = "Checking connection status…",
                                    color = InkMuted,
                                    fontFamily = Inter,
                                    fontSize = 14.5.sp,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                            is SettingsUiState.Error -> {
                                Text(
                                    text = state.message,
                                    color = Danger,
                                    fontFamily = Inter,
                                    fontSize = 14.5.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                            is SettingsUiState.Success -> {
                                val statusText = when (state.status) {
                                    "Connected" -> "Connected & Verified"
                                    "Incomplete (Agora keys missing)" -> "Agora Keys Missing"
                                    "Not configured" -> "No Credentials Configured"
                                    "Invalid credentials" -> "Invalid Credentials"
                                    else -> state.status
                                }
                                val statusColor = when (state.status) {
                                    "Connected" -> SuccessColor
                                    "Incomplete (Agora keys missing)" -> Color(0xFFF59E0B)
                                    "Invalid credentials" -> Danger
                                    else -> InkMuted
                                }
                                Text(
                                    text = statusText,
                                    color = statusColor,
                                    fontFamily = Inter,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                        }
                    }

                    Spacer(Modifier.width(12.dp))

                    val (badgeText, badgeBg, badgeDot) = when (val state = uiState) {
                        is SettingsUiState.Success -> when (state.status) {
                            "Connected" -> Triple("Connected", SuccessColor.copy(alpha = 0.2f), SuccessColor)
                            "Incomplete (Agora keys missing)" -> Triple("Agora Missing", Color(0xFFF59E0B).copy(alpha = 0.2f), Color(0xFFF59E0B))
                            "Invalid credentials" -> Triple("Invalid", Danger.copy(alpha = 0.2f), Danger)
                            else -> Triple("Not Configured", SignatureDim, InkFaint)
                        }
                        is SettingsUiState.Error -> Triple("Error", Danger.copy(alpha = 0.2f), Danger)
                        else -> Triple("Checking", SignatureDim, SignatureA)
                    }

                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(12.dp))
                            .background(badgeBg)
                            .padding(horizontal = 10.dp, vertical = 6.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(7.dp)
                                    .clip(CircleShape)
                                    .background(badgeDot)
                            )
                            Spacer(Modifier.width(6.dp))
                            Text(
                                text = badgeText,
                                color = Ink,
                                fontFamily = Inter,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // Expandable How-To Guide Toggle Button
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp))
                    .background(Panel)
                    .border(1.dp, InkFaint.copy(alpha = 0.2f), RoundedCornerShape(16.dp))
                    .clickable { showGuide = !showGuide }
                    .padding(horizontal = 16.dp, vertical = 14.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.weight(1f)
                    ) {
                        Text(text = "🔑", fontSize = 16.sp)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            text = "How do I get my Agora & Groq keys?",
                            color = Ink,
                            fontFamily = Inter,
                            fontSize = 13.5.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(10.dp))
                            .background(if (showGuide) SignatureA.copy(alpha = 0.2f) else SignatureDim)
                            .padding(horizontal = 10.dp, vertical = 5.dp)
                    ) {
                        Text(
                            text = if (showGuide) "Hide Guide" else "View Guide",
                            color = SignatureA,
                            fontFamily = Inter,
                            fontSize = 11.5.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }

            // Expandable Guide Content
            AnimatedVisibility(visible = showGuide) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 10.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(Panel.copy(alpha = 0.6f))
                        .border(1.dp, SignatureA.copy(alpha = 0.3f), RoundedCornerShape(16.dp))
                        .padding(16.dp)
                ) {
                    Text(
                        text = "1. App ID & App Certificate",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "Go to your Agora Console Projects page. Select your project, click Configure, and copy the App ID and Primary Certificate.",
                        color = InkMuted,
                        fontFamily = Inter,
                        fontSize = 12.5.sp,
                        lineHeight = 17.sp
                    )
                    Spacer(Modifier.height(8.dp))
                    ConsoleLinkButton(
                        label = "Open Agora Projects Console ↗",
                        onClick = { uriHandler.openUri(AGORA_PROJECTS_URL) }
                    )

                    Spacer(Modifier.height(14.dp))

                    Text(
                        text = "2. Customer ID & Customer Secret",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "Go to Developer Toolkit > RESTful API in Agora Console. Click 'Add a secret' and copy both the Customer ID and Customer Secret.",
                        color = InkMuted,
                        fontFamily = Inter,
                        fontSize = 12.5.sp,
                        lineHeight = 17.sp
                    )
                    Spacer(Modifier.height(8.dp))
                    ConsoleLinkButton(
                        label = "Open RESTful API Console ↗",
                        onClick = { uriHandler.openUri(AGORA_RESTFUL_URL) }
                    )

                    Spacer(Modifier.height(14.dp))

                    Text(
                        text = "3. Groq API Key (LLM Orchestration)",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "Go to Groq Console > API Keys. Click 'Create API Key' and copy your key (e.g. gsk_...).",
                        color = InkMuted,
                        fontFamily = Inter,
                        fontSize = 12.5.sp,
                        lineHeight = 17.sp
                    )
                    Spacer(Modifier.height(8.dp))
                    ConsoleLinkButton(
                        label = "Open Groq Console ↗",
                        onClick = { uriHandler.openUri(GROQ_KEYS_URL) }
                    )
                }
            }

            Spacer(Modifier.height(24.dp))

            // Form Fields
            Text(
                text = "CREDENTIAL DETAILS",
                color = InkFaint,
                fontFamily = Inter,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.2.sp
            )
            Spacer(Modifier.height(12.dp))

            StyledTextField(
                value = appId,
                onValueChange = { appId = it },
                label = "App ID",
                placeholder = "e.g. 970ab23...",
                imeAction = ImeAction.Next
            )

            Spacer(Modifier.height(12.dp))

            StyledTextField(
                value = appCert,
                onValueChange = { appCert = it },
                label = "App Certificate",
                placeholder = "e.g. 5d8e9a2...",
                isPassword = true,
                isPasswordVisible = certVisible,
                imeAction = ImeAction.Next,
                onToggleVisibility = { certVisible = !certVisible }
            )

            Spacer(Modifier.height(8.dp))
            ConsoleLinkButton(
                label = "Get App ID & Certificate from Agora ↗",
                onClick = { uriHandler.openUri(AGORA_PROJECTS_URL) }
            )

            Spacer(Modifier.height(16.dp))

            StyledTextField(
                value = customerId,
                onValueChange = { customerId = it },
                label = "Customer ID",
                placeholder = "e.g. 2389a0...",
                imeAction = ImeAction.Next
            )

            Spacer(Modifier.height(12.dp))

            StyledTextField(
                value = customerSecret,
                onValueChange = { customerSecret = it },
                label = "Customer Secret",
                placeholder = "e.g. f0128b...",
                isPassword = true,
                isPasswordVisible = secretVisible,
                imeAction = ImeAction.Next,
                onToggleVisibility = { secretVisible = !secretVisible }
            )

            Spacer(Modifier.height(8.dp))
            ConsoleLinkButton(
                label = "Get Customer ID & Secret from Agora ↗",
                onClick = { uriHandler.openUri(AGORA_RESTFUL_URL) }
            )

            Spacer(Modifier.height(16.dp))

            StyledTextField(
                value = groqApiKey,
                onValueChange = { groqApiKey = it },
                label = "Groq API Key (Optional)",
                placeholder = "e.g. gsk_...",
                isPassword = true,
                isPasswordVisible = groqVisible,
                imeAction = ImeAction.Done,
                onToggleVisibility = { groqVisible = !groqVisible }
            )

            Spacer(Modifier.height(8.dp))
            ConsoleLinkButton(
                label = "Get Groq API Key from console.groq.com ↗",
                onClick = { uriHandler.openUri(GROQ_KEYS_URL) }
            )

            Spacer(Modifier.height(28.dp))

            // Action Buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier
                        .weight(1f)
                        .height(50.dp)
                        .clip(RoundedCornerShape(25.dp))
                        .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB)))
                        .clickable {
                            viewModel.saveCredentials(appId, appCert, customerId, customerSecret, groqApiKey, onSuccess = onBack)
                        }
                ) {
                    Text(
                        text = "Save Credentials",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 14.5.sp,
                        fontWeight = FontWeight.Bold
                    )
                }

                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier
                        .height(50.dp)
                        .clip(RoundedCornerShape(25.dp))
                        .background(Panel)
                        .border(1.dp, Danger.copy(alpha = 0.5f), RoundedCornerShape(25.dp))
                        .clickable {
                            viewModel.removeCredentials()
                            appId = ""
                            appCert = ""
                            customerId = ""
                            customerSecret = ""
                            groqApiKey = ""
                        }
                        .padding(horizontal = 20.dp)
                ) {
                    Text(
                        text = "Remove",
                        color = Danger,
                        fontFamily = Inter,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun ConsoleLinkButton(
    label: String,
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(SignatureDim.copy(alpha = 0.6f))
            .border(1.dp, SignatureA.copy(alpha = 0.35f), RoundedCornerShape(10.dp))
            .clickable { onClick() }
            .padding(horizontal = 12.dp, vertical = 6.dp)
    ) {
        Text(
            text = label,
            color = SignatureA,
            fontFamily = Inter,
            fontSize = 11.5.sp,
            fontWeight = FontWeight.Bold
        )
    }
}

@Composable
private fun StyledTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    placeholder: String,
    isPassword: Boolean = false,
    isPasswordVisible: Boolean = false,
    imeAction: ImeAction = ImeAction.Next,
    onToggleVisibility: (() -> Unit)? = null
) {
    Column {
        Text(
            text = label,
            color = InkMuted,
            fontFamily = Inter,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.Medium
        )
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            placeholder = { Text(placeholder, color = InkFaint, fontFamily = Inter, fontSize = 13.5.sp) },
            textStyle = TextStyle(color = Ink, fontFamily = Inter, fontSize = 14.sp),
            visualTransformation = if (isPassword && !isPasswordVisible) PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(
                keyboardType = if (isPassword) KeyboardType.Ascii else KeyboardType.Text,
                imeAction = imeAction
            ),
            trailingIcon = if (isPassword && onToggleVisibility != null) {
                {
                    Text(
                        text = if (isPasswordVisible) "HIDE" else "SHOW",
                        color = SignatureA,
                        fontFamily = Inter,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier
                            .clickable(
                                indication = null,
                                interactionSource = remember { MutableInteractionSource() },
                                onClick = { onToggleVisibility() }
                            )
                            .padding(8.dp)
                    )
                }
            } else null,
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = Panel,
                unfocusedContainerColor = Panel,
                disabledContainerColor = Panel,
                focusedBorderColor = SignatureA,
                unfocusedBorderColor = InkFaint.copy(alpha = 0.35f),
                cursorColor = SignatureA
            ),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth()
        )
    }
}
