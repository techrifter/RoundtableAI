package com.techrifter.roundtableai.interview

import android.app.Application
import android.net.Uri
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.techrifter.roundtableai.network.InterviewProfilePreview
import com.techrifter.roundtableai.network.InterviewReport
import com.techrifter.roundtableai.network.SessionApi
import io.agora.conversational.api.AgentState
import io.agora.conversational.api.ConversationalAIAPIConfig
import io.agora.conversational.api.ConversationalAIAPIImpl
import io.agora.conversational.api.IConversationalAIAPIEventHandler
import io.agora.conversational.api.StateChangeEvent
import io.agora.conversational.api.Transcript
import io.agora.conversational.api.TranscriptRenderMode
import io.agora.conversational.api.TranscriptType
import io.agora.conversational.api.Turn
import io.agora.rtc2.ChannelMediaOptions
import io.agora.rtc2.Constants
import io.agora.rtc2.IRtcEngineEventHandler
import io.agora.rtc2.IRtcEngineEventHandler.AudioVolumeInfo
import io.agora.rtc2.RtcEngine
import io.agora.rtc2.RtcEngineConfig
import io.agora.rtc2.RtcEngineEx
import io.agora.rtm.RtmClient
import io.agora.rtm.RtmConfig
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

private const val TAG = "RoundtableRTM"

enum class InterviewStatus {
    IDLE, REQUESTING_SESSION, JOINING, LIVE, REPORT, ERROR
}

enum class SignalStatus { CONNECTING, CONNECTED, FAILED }

data class InterviewUiState(
    val status: InterviewStatus = InterviewStatus.IDLE,
    val errorMessage: String? = null,
)

data class ReportUiState(
    val status: String = "pending", // "pending" | "ready" | "failed" | "not_applicable"
    val report: InterviewReport? = null,
)

enum class ProfileStatus { NONE, UPLOADING, READY, FAILED }

data class ProfileUiState(
    val status: ProfileStatus = ProfileStatus.NONE,
    val profileId: String? = null,
    val profile: InterviewProfilePreview? = null,
    val errorMessage: String? = null,
)

class InterviewViewModel(application: Application) : AndroidViewModel(application) {
    private val sessionApi = SessionApi()
    private var rtcEngine: RtcEngineEx? = null
    private var rtmClient: RtmClient? = null
    private var conversationalApi: ConversationalAIAPIImpl? = null

    private val _uiState = MutableStateFlow(InterviewUiState())
    val uiState: StateFlow<InterviewUiState> = _uiState

    private val _agentState = MutableStateFlow(AgentState.IDLE)
    val agentState: StateFlow<AgentState> = _agentState

    private val _transcript = MutableStateFlow<List<TranscriptEntry>>(emptyList())
    val transcript: StateFlow<List<TranscriptEntry>> = _transcript

    private val _signalStatus = MutableStateFlow(SignalStatus.CONNECTING)
    val signalStatus: StateFlow<SignalStatus> = _signalStatus

    // 0f..1f, driven by RTC's real audio volume indication (not simulated) — the
    // candidate's own mic level and the agent's playback level, sampled ~5x/sec.
    private val _localAudioLevel = MutableStateFlow(0f)
    val localAudioLevel: StateFlow<Float> = _localAudioLevel

    private val _agentAudioLevel = MutableStateFlow(0f)
    val agentAudioLevel: StateFlow<Float> = _agentAudioLevel

    // Which persona is currently interviewing — the one piece of orchestrator
    // state actually worth showing the candidate. Polled from the backend
    // while live; null until the first turn completes.
    private val _currentPersona = MutableStateFlow<String?>(null)
    val currentPersona: StateFlow<String?> = _currentPersona

    private val _report = MutableStateFlow(ReportUiState())
    val report: StateFlow<ReportUiState> = _report

    private val _profileState = MutableStateFlow(ProfileUiState())
    val profileState: StateFlow<ProfileUiState> = _profileState

    private var candidateUid: Int = 0
    private var channelName: String = ""
    private var stagePollJob: kotlinx.coroutines.Job? = null

    // Keyed by "turnId-type" and kept in insertion order, per Agora's own reference
    // pattern for upserting streaming transcript rows as they arrive/finalize.
    private val transcriptByKey = LinkedHashMap<String, TranscriptEntry>()

    init {
        viewModelScope.launch { sessionApi.warmUp() }
    }

    private val rtcEventHandler = object : IRtcEngineEventHandler() {
        override fun onJoinChannelSuccess(channel: String?, uid: Int, elapsed: Int) {
            _uiState.value = _uiState.value.copy(status = InterviewStatus.LIVE)
        }

        override fun onError(err: Int) {
            _uiState.value = InterviewUiState(
                status = InterviewStatus.ERROR,
                errorMessage = "Agora RTC error code $err",
            )
        }

        override fun onUserOffline(uid: Int, reason: Int) {
            // The agent (uid 0) or candidate left; end-of-call handling lives in endInterview().
        }

        override fun onAudioRouteChanged(routing: Int) {
            applyAiAudioParameters(routing)
        }

        /**
         * Diagnostics for agent-voice quality. frozenRate/packet loss point at the
         * network or the jitter buffer; a clean network here means any distortion is
         * downstream of RTC (server-side TTS, or local processing/CPU starvation).
         */
        override fun onRemoteAudioStats(stats: IRtcEngineEventHandler.RemoteAudioStats?) {
            stats ?: return
            // Logged unconditionally: silence from a conditional log is ambiguous —
            // it cannot distinguish "stream is healthy" from "callback never fired".
            Log.d(
                TAG,
                "agentAudio loss=${stats.audioLossRate}% frozen=${stats.frozenRate}% " +
                    "jitter=${stats.jitterBufferDelay}ms netDelay=${stats.networkTransportDelay}ms " +
                    "quality=${stats.quality} rxBitrate=${stats.receivedBitrate}kbps",
            )
        }

        override fun onAudioVolumeIndication(speakers: Array<AudioVolumeInfo>?, totalVolume: Int) {
            speakers?.forEach { info ->
                // Agora reports the local mic under the caller's own uid; anything
                // else in this channel is the agent (its fixed rtc uid is 0).
                val level = (info.volume / 255f).coerceIn(0f, 1f)
                if (info.uid == candidateUid) {
                    _localAudioLevel.value = level
                } else {
                    _agentAudioLevel.value = level
                }
            }
        }
    }

    private val conversationalEventHandler = object : IConversationalAIAPIEventHandler {
        override fun onAgentStateChanged(agentUserId: String, event: StateChangeEvent) {
            Log.d(TAG, "onAgentStateChanged: agentUserId=$agentUserId state=${event.state}")
            _agentState.value = event.state
        }

        override fun onTranscriptUpdated(agentUserId: String, transcript: Transcript) {
            Log.d(
                TAG,
                "onTranscriptUpdated: turnId=${transcript.turnId} type=${transcript.type} " +
                    "status=${transcript.status} text=${transcript.text}",
            )
            val speaker = if (transcript.type == TranscriptType.AGENT) Speaker.AGENT else Speaker.CANDIDATE
            val key = "${transcript.turnId}-${transcript.type}"
            transcriptByKey[key] = TranscriptEntry(
                turnId = transcript.turnId,
                speaker = speaker,
                text = transcript.text,
                isFinal = transcript.status.name == "END",
            )
            _transcript.value = transcriptByKey.values.sortedBy { it.turnId }
        }

        override fun onAgentInterrupted(agentUserId: String, event: io.agora.conversational.api.InterruptEvent) {}
        override fun onAgentMetrics(agentUserId: String, metric: io.agora.conversational.api.Metric) {}

        override fun onTurnFinished(agentUserId: String, turn: Turn) {
            // Latency breakdown is useful for our own debugging, not for the
            // candidate's screen — logged only, not surfaced in the UI.
            Log.d(TAG, "onTurnFinished: turnId=${turn.turnId} e2e=${turn.e2eLatency}ms")
        }
        override fun onAgentError(agentUserId: String, error: io.agora.conversational.api.ModuleError) {}
        override fun onMessageError(agentUserId: String, error: io.agora.conversational.api.MessageError) {}
        override fun onMessageReceiptUpdated(agentUserId: String, receipt: io.agora.conversational.api.MessageReceipt) {}
        override fun onAgentVoiceprintStateChanged(
            agentUserId: String,
            event: io.agora.conversational.api.VoiceprintStateChangeEvent,
        ) {}
        override fun onDebugLog(log: String) {}
    }

    /**
     * AEC tuning per Agora's Conversational AI client setup. nlpAlgRoute differs for
     * headset (1) vs speaker/earpiece (0) — echo coupling differs. Agora documents it
     * as route-dependent without giving the mapping; this follows their sample.
     */
    private fun applyAiAudioParameters(routing: Int) {
        val engine = rtcEngine ?: return
        val isHeadsetRoute = routing == Constants.AUDIO_ROUTE_HEADSET ||
            routing == Constants.AUDIO_ROUTE_HEADSETNOMIC ||
            routing == Constants.AUDIO_ROUTE_BLUETOOTH_DEVICE_HFP ||
            routing == Constants.AUDIO_ROUTE_BLUETOOTH_DEVICE_A2DP ||
            routing == Constants.AUDIO_ROUTE_USB_HEADSET
        val nlpAlgRoute = if (isHeadsetRoute) 1 else 0

        listOf(
            "{\"che.audio.aec.split_srate_for_48k\":16000}",
            "{\"che.audio.sf.enabled\":true}",
            "{\"che.audio.sf.stftType\":6}",
            "{\"che.audio.sf.ainlpLowLatencyFlag\":1}",
            "{\"che.audio.sf.procChainMode\":1}",
            "{\"che.audio.sf.nlpDynamicMode\":1}",
            "{\"che.audio.sf.nlpAlgRoute\":$nlpAlgRoute}",
            "{\"che.audio.sf.ainlpModelPref\":10}",
            "{\"che.audio.agc.enable\":false}",
            // The AI noise-suppression knobs (ainsLowLatencyFlag, nsngAlgRoute,
            // ainsModelPref, nsngPredefAgg) are deliberately omitted — the ains
            // extension is no longer loaded, so they would configure nothing.
        ).forEach { engine.setParameters(it) }
    }

    /** Uploads the candidate's own JD + resume; pass null for both to use the
     * backend's bundled sample pair instead (the quick-demo fallback). Parsing
     * happens up front so the pre-join screen can show what the panel actually
     * understood before the candidate commits to joining the call. */
    fun analyzeProfile(jdUri: Uri?, resumeUri: Uri?) {
        _profileState.value = ProfileUiState(status = ProfileStatus.UPLOADING)
        viewModelScope.launch {
            try {
                val result = sessionApi.uploadJd(getApplication(), jdUri, resumeUri)
                _profileState.value = ProfileUiState(
                    status = ProfileStatus.READY,
                    profileId = result.profileId,
                    profile = result.profile,
                )
            } catch (t: Throwable) {
                _profileState.value = ProfileUiState(
                    status = ProfileStatus.FAILED,
                    errorMessage = t.message ?: "Couldn't read that JD/resume",
                )
            }
        }
    }

    fun resetProfile() {
        _profileState.value = ProfileUiState()
    }

    fun startInterview() {
        val profileId = _profileState.value.profileId ?: return
        if (_uiState.value.status == InterviewStatus.REQUESTING_SESSION ||
            _uiState.value.status == InterviewStatus.JOINING
        ) {
            return
        }
        _uiState.value = InterviewUiState(status = InterviewStatus.REQUESTING_SESSION)

        viewModelScope.launch {
            try {
                val session = sessionApi.startSession(profileId)
                _uiState.value = _uiState.value.copy(status = InterviewStatus.JOINING)
                joinChannel(
                    appId = session.appId,
                    channelName = session.channelName,
                    token = session.candidateToken,
                    uid = session.candidateUid,
                    rtmToken = session.rtmToken,
                )
            } catch (t: Throwable) {
                _uiState.value = InterviewUiState(
                    status = InterviewStatus.ERROR,
                    errorMessage = t.message ?: "Failed to start session",
                )
            }
        }
    }

    private fun joinChannel(appId: String, channelName: String, token: String, uid: Int, rtmToken: String) {
        candidateUid = uid
        this.channelName = channelName
        startStagePolling(channelName)
        val engine = RtcEngine.create(
            RtcEngineConfig().apply {
                mContext = getApplication()
                mAppId = appId
                mEventHandler = rtcEventHandler
                // Agora's documented config for Conversational AI Engine client apps:
                // the AI-tuned audio scenario expects the broadcasting channel profile,
                // not the generic peer-to-peer communication profile.
                mChannelProfile = Constants.CHANNEL_PROFILE_LIVE_BROADCASTING
                mAudioScenario = Constants.AUDIO_SCENARIO_AI_CLIENT
            }
        ) as RtcEngineEx
        rtcEngine = engine

        // AEC only — barge-in over the phone speaker depends on it. The AI noise
        // suppression model starved the audio thread on real hardware and distorted
        // agent playback; the core SDK's classic DSP handles it instead.
        engine.loadExtensionProvider("ai_echo_cancellation_extension")

        engine.enableAudio()
        applyAiAudioParameters(Constants.AUDIO_ROUTE_DEFAULT)
        // 200ms sampling, light smoothing, VAD reporting on — feeds the live
        // level indicators for both the candidate's mic and the agent's voice.
        engine.enableAudioVolumeIndication(200, 3, true)

        val options = ChannelMediaOptions().apply {
            clientRoleType = Constants.CLIENT_ROLE_BROADCASTER
            channelProfile = Constants.CHANNEL_PROFILE_LIVE_BROADCASTING
        }
        engine.joinChannel(token, channelName, uid, options)

        setUpTranscripts(engine, appId, uid, rtmToken, channelName)
    }

    private fun setUpTranscripts(engine: RtcEngineEx, appId: String, uid: Int, rtmToken: String, channelName: String) {
        Log.d(TAG, "setUpTranscripts: starting for channel=$channelName uid=$uid appId=$appId")
        _signalStatus.value = SignalStatus.CONNECTING
        val rtm = try {
            RtmClient.create(RtmConfig.Builder(appId, uid.toString()).build())
        } catch (t: Throwable) {
            Log.e(TAG, "RtmClient.create failed", t)
            _signalStatus.value = SignalStatus.FAILED
            return
        }
        Log.d(TAG, "setUpTranscripts: RtmClient.create succeeded, calling login")
        rtmClient = rtm

        rtm.login(rtmToken, object : io.agora.rtm.ResultCallback<Void> {
            override fun onSuccess(responseInfo: Void?) {
                Log.d(TAG, "RTM login succeeded for channel=$channelName uid=$uid")
                val api = ConversationalAIAPIImpl(
                    ConversationalAIAPIConfig(
                        rtcEngine = engine,
                        rtmClient = rtm,
                        renderMode = TranscriptRenderMode.Word,
                        enableLog = true,
                        enableRenderModeFallback = true,
                    )
                )
                conversationalApi = api
                api.addHandler(conversationalEventHandler)
                api.subscribeMessage(channelName) { error ->
                    if (error != null) {
                        Log.e(TAG, "subscribeMessage failed: $error")
                        _signalStatus.value = SignalStatus.FAILED
                    } else {
                        Log.d(TAG, "subscribeMessage succeeded for channel=$channelName")
                        _signalStatus.value = SignalStatus.CONNECTED
                    }
                }
            }

            override fun onFailure(errorInfo: io.agora.rtm.ErrorInfo?) {
                Log.e(TAG, "RTM login failed: $errorInfo")
                _signalStatus.value = SignalStatus.FAILED
                // Voice interview still works without live transcripts; not fatal.
            }
        })
        Log.d(TAG, "setUpTranscripts: rtm.login() call issued, awaiting callback")

        viewModelScope.launch {
            delay(8000)
            if (_signalStatus.value == SignalStatus.CONNECTING) {
                Log.e(TAG, "WATCHDOG: neither onSuccess nor onFailure fired within 8s of rtm.login()")
            }
        }
    }

    private fun startStagePolling(channelName: String) {
        stagePollJob?.cancel()
        stagePollJob = viewModelScope.launch {
            while (true) {
                sessionApi.getCurrentStage(channelName)?.let { _currentPersona.value = it }
                delay(3000)
            }
        }
    }

    fun endInterview() {
        val endedChannel = channelName
        stagePollJob?.cancel()
        stagePollJob = null
        conversationalApi?.destroy()
        conversationalApi = null
        rtmClient?.logout(object : io.agora.rtm.ResultCallback<Void> {
            override fun onSuccess(responseInfo: Void?) {}
            override fun onFailure(errorInfo: io.agora.rtm.ErrorInfo?) {}
        })
        rtmClient?.release()
        rtmClient = null
        rtcEngine?.leaveChannel()
        RtcEngine.destroy()
        rtcEngine = null
        transcriptByKey.clear()
        _transcript.value = emptyList()
        _agentState.value = AgentState.IDLE
        _signalStatus.value = SignalStatus.CONNECTING
        _localAudioLevel.value = 0f
        _agentAudioLevel.value = 0f
        _currentPersona.value = null
        // Report generation is kicked off server-side once the panel reaches its
        // final stage, well before the candidate taps End in most cases — so this
        // screen is usually just a short wait, not a cold start.
        _report.value = ReportUiState()
        _uiState.value = InterviewUiState(status = InterviewStatus.REPORT)
        viewModelScope.launch {
            // Tells the backend to score whatever was covered, so ending early still
            // yields a report. Best-effort — the poll loop below reflects real state
            // either way.
            sessionApi.endSession(endedChannel)
            startReportPolling(endedChannel)
        }
    }

    private fun startReportPolling(channelName: String) {
        if (channelName.isBlank()) {
            _report.value = ReportUiState(status = "failed")
            return
        }
        viewModelScope.launch {
            // Up to ~2 minutes: report generation is one off-critical-path LLM
            // call, but Groq latency/rate limits can occasionally push it out.
            repeat(60) {
                val result = sessionApi.getReport(channelName)
                if (result != null) {
                    _report.value = ReportUiState(status = result.status, report = result.report)
                    if (result.status in TERMINAL_REPORT_STATUSES) return@launch
                }
                delay(2000)
            }
            // Covers every non-terminal status ("pending" and "not_started" alike)
            // rather than only "pending" — a status the backend never resolves
            // would otherwise poll forever with no exit.
            if (_report.value.status !in TERMINAL_REPORT_STATUSES) {
                _report.value = _report.value.copy(status = "failed")
            }
        }
    }

    private companion object {
        val TERMINAL_REPORT_STATUSES = setOf("ready", "failed", "not_applicable")
    }

    /** Leaves the report screen and resets to the pre-join state for another run. */
    fun startNewInterview() {
        _report.value = ReportUiState()
        _uiState.value = InterviewUiState(status = InterviewStatus.IDLE)
    }

    override fun onCleared() {
        super.onCleared()
        stagePollJob?.cancel()
        conversationalApi?.destroy()
        rtcEngine?.leaveChannel()
        RtcEngine.destroy()
        rtcEngine = null
    }
}
