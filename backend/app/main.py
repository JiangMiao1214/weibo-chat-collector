from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.ocr import router as ocr_router
from .services.ocr_worker import get_ocr_worker
from .api.insights import router as insights_router
from .api.context_recovery import router as context_recovery_router
from .api.message_references import router as message_references_router
from .api.target_management import router as target_management_router
from .api.archives import router as archives_router
from .api.archive_maintenance import router as archive_maintenance_router
from .services.archive_worker import get_archive_worker
from .api.browser_captures import router as browser_captures_router
from .api.browser_login import router as browser_login_router
from .api.collection_jobs import router as collection_jobs_router
from .api.attachment_images import router as attachment_images_router
from .api.messages import router as messages_router
from .api.message_view import router as message_view_router
from .api.quote_context import router as quote_context_router
from .api.single_group_collection import router as single_group_collection_router
from .api.weibo_api import router as weibo_api_router
from .api.weibo_verifications import router as weibo_verifications_router
from .database import initialize_database
from .services.collection_worker import get_collection_worker
from .services.browser_login_manager import get_browser_login_manager
from .settings import get_settings
from .services.development_guidance import read_development_reminder


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        logging.getLogger("uvicorn.error").info(read_development_reminder())
    except (OSError, ValueError) as error:
        logging.getLogger("uvicorn.error").warning("[TDD] 无法读取开发规则：%s", error)
    initialize_database()
    worker = get_collection_worker()
    worker.start()
    archive_worker = get_archive_worker()
    archive_worker.start()
    ocr_worker = get_ocr_worker()
    ocr_worker.start()
    try:
        yield
    finally:
        get_browser_login_manager().shutdown()
        ocr_worker.stop()
        archive_worker.stop()
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


app.include_router(ocr_router)
app.include_router(insights_router)
app.include_router(context_recovery_router)
app.include_router(message_references_router)
app.include_router(target_management_router)
app.include_router(browser_captures_router)
app.include_router(browser_login_router)
app.include_router(messages_router)
app.include_router(archives_router)
app.include_router(archive_maintenance_router)
app.include_router(attachment_images_router)
app.include_router(message_view_router)
app.include_router(quote_context_router)
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
