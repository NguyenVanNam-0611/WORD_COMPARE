from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.api.upload import (
    router as upload_router,
    start_scheduler,
    stop_scheduler,
)
from src.api.status import router as status_router
from src.core.queue import start_worker
from src.worker.processor import process_job

app = FastAPI(
    title="Document Compare API",
    version="1.0.0",
)

_started = False

# Optional CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded files
app.mount(
    "/uploads",
    StaticFiles(directory="uploads"),
    name="uploads",
)

# Routers
app.include_router(upload_router)
app.include_router(status_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "service": "document-compare-api",
        "status": "running",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "worker_started": _started,
    }


@app.on_event("startup")
async def on_startup():
    global _started

    if _started:
        return

    print("====================================")
    print("[STARTUP] Starting document compare service")
    print("[STARTUP] Starting worker")
    start_worker(process_job)

    print("[STARTUP] Starting cleanup scheduler")
    start_scheduler()

    _started = True

    print("[STARTUP] Service ready")
    print("====================================")


@app.on_event("shutdown")
async def on_shutdown():
    print("====================================")
    print("[SHUTDOWN] Stopping cleanup scheduler")
    stop_scheduler()
    print("[SHUTDOWN] Service stopped")
    print("====================================")