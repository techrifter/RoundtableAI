package com.techrifter.roundtableai.interview

enum class Speaker { AGENT, CANDIDATE }

data class TranscriptEntry(
    val turnId: Long,
    val speaker: Speaker,
    val text: String,
    val isFinal: Boolean,
)
