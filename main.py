# Main FastAPI Application Entrypoint
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from config import settings
from api.router import api_router

# Ensure safe UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

from services.storage import storage



# Mount local storage for static serving if running in local mode or local dir exists
if storage.local_dir.exists():
    app.mount(f"/{storage.local_dir.name}", StaticFiles(directory=str(storage.local_dir)), name="storage")

# Mount standalone PDF viewer static directory
viewer_dir = Path(__file__).resolve().parent / "viewer"
if viewer_dir.exists():
    app.mount("/viewer", StaticFiles(directory=str(viewer_dir)), name="viewer")


# Enable CORS for React Native / Expo / Web
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "docs": "/docs",
    }
