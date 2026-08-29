from dataclasses import dataclass, field

UNCERTAIN_PHRASES = [
    "don't know", "dont know", "do not know", "not sure", "not really", "no idea", "no clue",
    "haven't", "havent", "have not", "not have",
    "no experience", "not familiar", "not aware",
    "never done", "never used", "never worked",
    "not worked", "not done", "not used", "not tried",
    "can't recall", "cant recall", "don't remember", "dont remember",
]

# Below this word count, treat an answer as too thin to signal real engagement,
# even without an explicit "I don't know" — regardless, this alone isn't enough
# to fast-exit a topic (see is_low_engagement), only paired with an uncertain phrase.
THIN_ANSWER_WORD_COUNT = 4


@dataclass
class AnswerSignal:
    matched_topics: set[str] = field(default_factory=set)
    is_low_engagement: bool = False


def analyze_answer(text: str, topic_pool: list[str]) -> AnswerSignal:
    """Deterministic read of one answer: which JD topics it touched, and whether it
    looks low-engagement. No LLM call — runs on every turn, so it must stay fast and
    side-effect-free; it's recomputed per request rather than stored.
    """
    lowered = text.lower()
    word_count = len(text.split())

    matched = {topic for topic in topic_pool if topic.lower() in lowered}

    has_uncertain_phrase = any(phrase in lowered for phrase in UNCERTAIN_PHRASES)
    is_thin = word_count <= THIN_ANSWER_WORD_COUNT
    # Either alone is enough: "I don't know" is explicit, and a genuine "no
    # experience with X" is usually short anyway.
    is_low_engagement = has_uncertain_phrase or is_thin

    return AnswerSignal(matched_topics=matched, is_low_engagement=is_low_engagement)
