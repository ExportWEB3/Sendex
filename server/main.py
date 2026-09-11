from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
import os

from database import create_tables
from api import smtp, inbox, campaign, recipient, queue, warmup, monitoring, logs, system, auth, tracking, fancy_email_template
from api import list as recipient_list
from api import tags, unsubscribe, assistant, resend_webhook, replies, bulk_import

# Load env
from env_loader import load_app_env
load_app_env()

API_APP_KEY = os.getenv("API_APP_KEY", "")

# Paths that skip the app-key check (webhooks, tracking, health)
APP_KEY_EXEMPT_PREFIXES = (
    "/api/resend/",
    "/api/tracking",
    "/api/track/",
    "/api/unsubscribe/",
    "/health",
)


class AppKeyMiddleware(BaseHTTPMiddleware):
    """Reject API requests that don't carry the correct X-App-Key header."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if API_APP_KEY and path.startswith("/api/"):
            if not any(path.startswith(p) for p in APP_KEY_EXEMPT_PREFIXES):
                key = request.headers.get("X-App-Key", "")
                if key != API_APP_KEY:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Forbidden"},
                    )
        return await call_next(request)


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Set cache policy and baseline browser security headers."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Hashed asset files (JS/CSS with content hash) - cache for 1 year
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        # HTML pages - always revalidate to pick up new asset references
        elif path == "/" or not path.startswith("/api"):
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type:
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create database tables
    create_tables()
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="FLEETCTRL-X API",
    description="Self-hosted email warm-up and campaign system",
    version="1.0.0",
    lifespan=lifespan
)

# Add cache control middleware
app.add_middleware(CacheControlMiddleware)

# Add API key middleware
app.add_middleware(AppKeyMiddleware)

# Include API routers
app.include_router(auth.router)
app.include_router(smtp.router)
app.include_router(inbox.router)
app.include_router(campaign.router)
app.include_router(recipient.router)
app.include_router(queue.router)
app.include_router(warmup.router)
app.include_router(monitoring.router)
app.include_router(logs.router)
app.include_router(recipient_list.router)
app.include_router(fancy_email_template.router)
app.include_router(tags.router)
app.include_router(unsubscribe.router)
app.include_router(system.router)
app.include_router(tracking.router)
app.include_router(resend_webhook.router)
app.include_router(replies.router)
app.include_router(bulk_import.router)
app.include_router(assistant.router)

# Mount static files for dashboard UI (serve built React app from Frontend/dist)
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Frontend", "dist")
assets_dir = os.path.join(static_dir, "assets")
uploads_dir = os.environ.get("UPLOADS_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
os.makedirs(uploads_dir, exist_ok=True)

# Mount uploads directory for attachment previews
if os.path.exists(uploads_dir):
    app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Mount assets directory for JS/CSS bundles
if os.path.exists(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


def _serve_index():
    """Serve index.html with no-cache headers"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )
    return {"status": "working", "message": "FLEETCTRL-X API is running!"}


@app.get("/")
def home():
    """Serve dashboard React app"""
    return _serve_index()


# SPA catch-all routes - serve index.html for client-side routing
@app.get("/inboxes")
@app.get("/campaigns")
@app.get("/lists")
@app.get("/queue")
@app.get("/smtp")
@app.get("/settings")
@app.get("/replies")
@app.get("/replies/resend")
@app.get("/replies/ses")
@app.get("/replies/smtp")
@app.get("/templates")
@app.get("/monitoring")
@app.get("/login")
@app.get("/register")
@app.get("/verify-email")
@app.get("/forgot-password")
@app.get("/reset-password")
def spa_routes():
    """Serve React app for SPA routes"""
    return _serve_index()


@app.get("/health")
def health_check():
    return {"status": "healthy"}