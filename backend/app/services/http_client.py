import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Shared, reused HTTP client with connection pooling/keep-alive. Creating a
    fresh httpx.AsyncClient per request (the previous pattern) meant every single
    turn paid a full DNS + TCP + TLS handshake to api.groq.com — a real, avoidable
    chunk of latency, worst on the very first request in a session when nothing is
    warm yet. One shared client for the process lifetime avoids that entirely.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client
