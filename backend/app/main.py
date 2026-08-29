import logging

from fastapi import FastAPI

from app.api.jd import router as jd_router
from app.api.llm import router as llm_router
from app.api.session import router as session_router

# Configure logging and create the FastAPI app, including all routers.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

app = FastAPI(title="RoundtableAI Backend")

app.include_router(llm_router)
app.include_router(session_router)
app.include_router(jd_router)

# Health check endpoint for monitoring the backend's availability.
@app.get("/health")
async def health():
    return {"status": "ok"}
