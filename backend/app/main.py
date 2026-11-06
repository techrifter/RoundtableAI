import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.account_deletion import account_deletion_worker
from app.api.jd import router as jd_router
from app.api.legal import router as legal_router
from app.api.llm import router as llm_router
from app.api.session import router as session_router
from app.api.user import router as user_router
from app.auth import init_firebase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_firebase()
    worker = asyncio.create_task(account_deletion_worker())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="RoundtableAI Backend", lifespan=lifespan)

app.include_router(llm_router)
app.include_router(session_router)
app.include_router(jd_router)
app.include_router(user_router)
app.include_router(legal_router)

# Health check endpoint for monitoring the backend's availability.
# Intentionally unauthenticated — used by the Android app's warm-up ping.
@app.get("/health")
async def health():
    return {"status": "ok"}
