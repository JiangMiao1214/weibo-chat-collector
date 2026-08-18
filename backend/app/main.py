from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.browser_captures import router as browser_captures_router
from .api.collection_jobs import router as collection_jobs_router
from .api.messages import router as messages_router
from .api.single_group_collection import router as single_group_collection_router
from .api.weibo_api import router as weibo_api_router
from .api.weibo_verifications import router as weibo_verifications_router
from .database import initialize_database
from .services.collection_worker import get_collection_worker
from .settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    worker = get_collection_worker()
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="Weibo Chat Collector", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(browser_captures_router)
app.include_router(messages_router)
app.include_router(collection_jobs_router)
app.include_router(single_group_collection_router)
app.include_router(weibo_api_router)
app.include_router(weibo_verifications_router)


@app.get("/health")
def health() -> dict[str, str | int]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "default_collection_days": settings.default_collection_days,
        "delete_mode": settings.delete_mode,
    }


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "Weibo Chat Collector", "status": "initialized"}
