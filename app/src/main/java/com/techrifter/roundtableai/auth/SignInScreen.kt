package com.techrifter.roundtableai.auth

import android.app.Activity
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.techrifter.roundtableai.R
import com.techrifter.roundtableai.interview.Inter
import com.techrifter.roundtableai.interview.VoicePresence
import io.agora.conversational.api.AgentState

private val Backdrop = Color(0xFF0A0A12)
private val Ink = Color(0xFFF6F4FF)
private val InkMuted = Color(0xFFACA6C4)
private val InkFaint = Color(0xFF625D7A)
private val SignatureA = Color(0xFF8B5CF6)
private val SignatureB = Color(0xFFFF3DA6)
private val Danger = Color(0xFFFF4D6D)
private val InputBg = Color(0xFF14121F)
private val InputBorder = Color(0xFF2C273F)
private val GoogleButtonBg = Color(0xFF1E1B2E)
private val GoogleButtonBorder = Color(0xFF3A3450)

@Composable
fun SignInScreen(
    authViewModel: AuthViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val authState by authViewModel.authState.collectAsState()
    val isGoogleSigningIn by authViewModel.isGoogleSigningIn.collectAsState()
    val context = LocalContext.current
    val focusManager = LocalFocusManager.current

    var isSignUp by remember { mutableStateOf(false) }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Brush.radialGradient(colors = listOf(Color(0xFF241C3A), Backdrop)))
            .windowInsetsPadding(WindowInsets.safeDrawing),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 32.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "LIVE VOICE INTERVIEW PANEL",
                color = SignatureA,
                fontFamily = Inter,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 2.4.sp,
            )
            Spacer(Modifier.height(20.dp))
            VoicePresence(
                agentState = AgentState.IDLE,
                audioLevel = 0f,
                modifier = Modifier.size(170.dp),
            )
            Spacer(Modifier.height(28.dp))
            Text(
                text = "Roundtable AI",
                color = Ink,
                fontSize = 36.sp,
                fontFamily = Inter,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = (-1.2).sp,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = if (isSignUp) "Create your account to start" else "Sign in to practice your interviews",
                color = InkMuted,
                fontFamily = Inter,
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(28.dp))

            // Error display
            if (authState is AuthState.Error) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(Danger.copy(alpha = 0.12f))
                        .border(1.dp, Danger.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) {
                    Text(
                        text = (authState as AuthState.Error).message,
                        color = Danger,
                        fontSize = 12.5.sp,
                        lineHeight = 17.sp,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                Spacer(Modifier.height(18.dp))
            }

            // Email input
            OutlinedTextField(
                value = email,
                onValueChange = {
                    email = it
                    authViewModel.clearError()
                },
                label = { Text("Email address") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Email,
                    imeAction = ImeAction.Next,
                ),
                keyboardActions = KeyboardActions(
                    onNext = { focusManager.moveFocus(FocusDirection.Down) }
                ),
                shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = Ink,
                    unfocusedTextColor = Ink,
                    focusedContainerColor = InputBg,
                    unfocusedContainerColor = InputBg,
                    focusedBorderColor = SignatureA,
                    unfocusedBorderColor = InputBorder,
                    focusedLabelColor = SignatureA,
                    unfocusedLabelColor = InkMuted,
                    cursorColor = SignatureA,
                ),
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(Modifier.height(14.dp))

            // Password input
            OutlinedTextField(
                value = password,
                onValueChange = {
                    password = it
                    authViewModel.clearError()
                },
                label = { Text("Password") },
                singleLine = true,
                visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(
                    onDone = {
                        focusManager.clearFocus()
                        if (isSignUp) authViewModel.signUpWithEmail(email, password)
                        else authViewModel.signInWithEmail(email, password)
                    }
                ),
                trailingIcon = {
                    Text(
                        text = if (passwordVisible) "Hide" else "Show",
                        color = SignatureA,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication = null,
                                onClick = { passwordVisible = !passwordVisible }
                            )
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                    )
                },
                shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = Ink,
                    unfocusedTextColor = Ink,
                    focusedContainerColor = InputBg,
                    unfocusedContainerColor = InputBg,
                    focusedBorderColor = SignatureA,
                    unfocusedBorderColor = InputBorder,
                    focusedLabelColor = SignatureA,
                    unfocusedLabelColor = InkMuted,
                    cursorColor = SignatureA,
                ),
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(Modifier.height(20.dp))

            // Primary action button (Sign In / Sign Up)
            val isLoading = authState is AuthState.Loading
            val isAuthenticating = isLoading || isGoogleSigningIn
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Brush.horizontalGradient(listOf(SignatureA, SignatureB)))
                    .clickable(
                        enabled = !isAuthenticating,
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = {
                            focusManager.clearFocus()
                            if (isSignUp) authViewModel.signUpWithEmail(email, password)
                            else authViewModel.signInWithEmail(email, password)
                        }
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (isLoading) {
                    CircularProgressIndicator(
                        color = Ink,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(24.dp),
                    )
                } else {
                    Text(
                        text = if (isSignUp) "Create Account" else "Sign In",
                        color = Ink,
                        fontFamily = Inter,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            Spacer(Modifier.height(18.dp))

            // Toggle Sign In / Sign Up
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center,
            ) {
                Text(
                    text = if (isSignUp) "Already have an account? " else "Don't have an account? ",
                    color = InkMuted,
                    fontFamily = Inter,
                    fontSize = 13.5.sp,
                )
                Text(
                    text = if (isSignUp) "Sign In" else "Sign Up",
                    color = SignatureA,
                    fontFamily = Inter,
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = {
                            isSignUp = !isSignUp
                            authViewModel.clearError()
                        }
                    )
                )
            }

            Spacer(Modifier.height(22.dp))

            // Divider: OR
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                HorizontalDivider(modifier = Modifier.weight(1f), color = Color(0xFF221F32))
                Text(
                    text = "  OR  ",
                    color = InkFaint,
                    fontFamily = Inter,
                    fontSize = 11.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 1.sp,
                )
                HorizontalDivider(modifier = Modifier.weight(1f), color = Color(0xFF221F32))
            }

            Spacer(Modifier.height(22.dp))

            // Google Sign-In button
            GoogleSignInButton(
                isLoading = isGoogleSigningIn,
                enabled = !isAuthenticating,
            ) {
                (context as? Activity)?.let { authViewModel.signInWithGoogle(it) }
            }

            Spacer(Modifier.height(24.dp))

            Text(
                text = "By continuing, you acknowledge our Privacy Policy.\nYour information is processed to provide the service.",
                color = InkFaint,
                fontFamily = Inter,
                fontSize = 11.sp,
                textAlign = TextAlign.Center,
                lineHeight = 16.sp,
                modifier = Modifier.clickable {
                    context.startActivity(
                        Intent(
                            Intent.ACTION_VIEW,
                            Uri.parse("https://sites.google.com/view/roundtableai-privacy-policy"),
                        )
                    )
                },
            )
        }
    }
}

@Composable
private fun GoogleSignInButton(
    isLoading: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(50.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(GoogleButtonBg)
            .border(1.dp, GoogleButtonBorder, RoundedCornerShape(14.dp))
            .clickable(
                enabled = enabled,
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClick,
            )
            .padding(horizontal = 20.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        if (isLoading) {
            CircularProgressIndicator(
                color = Ink,
                strokeWidth = 2.dp,
                modifier = Modifier.size(22.dp),
            )
        } else {
            Icon(
                painter = painterResource(id = R.drawable.ic_google),
                contentDescription = "Google",
                tint = Color.Unspecified,
                modifier = Modifier.size(20.dp),
            )
            Spacer(Modifier.width(12.dp))
            Text(
                text = "Continue with Google",
                color = Ink,
                fontFamily = Inter,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
            )
        }
    }
}
