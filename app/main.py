from fastapi import FastAPI
import asyncio
import sys

# -------------------------------------------------------------------
# 🩵 FIX for Python 3.13 + Windows + Playwright
# -------------------------------------------------------------------
# Playwright requires a Selector-based event loop that supports
# subprocess spawning (Proactor does not).  We set it here so it
# takes effect before any async code or FastAPI/Uvicorn loop starts.
# -------------------------------------------------------------------
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        print("[init] Using WindowsSelectorEventLoopPolicy (Playwright fix applied).")
    except Exception as e:
        print(f"[init] Failed to set event loop policy: {e}")
# -------------------------------------------------------------------

from fastapi.middleware.cors import CORSMiddleware
from app.controllers import profile_controller      # your router
from app.core.config import settings
from app.core.itr_middleware import ITRMiddleware   # custom middleware

# -------------------------------------------------------------------
#  FastAPI Application Configuration
# -------------------------------------------------------------------
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "ITR Profile Automation microservice handling automation "
        "and heavy processing tasks using Playwright."
    ),
)

# -------------------------------------------------------------------
#  Global Middleware
# -------------------------------------------------------------------
# 1️⃣ Enable CORS (configure specific origins in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2️⃣ Custom logging / timing middleware
app.add_middleware(ITRMiddleware)

# -------------------------------------------------------------------
#  Health Check Route
# -------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Simple uptime check for monitoring and load balancers."""
    return {"status": "ok", "service": settings.PROJECT_NAME}

# -------------------------------------------------------------------
#  Core Routers
# -------------------------------------------------------------------
app.include_router(profile_controller.router, prefix=settings.API_PREFIX)

# -------------------------------------------------------------------
#  Local Dev Entry Point
# -------------------------------------------------------------------
if __name__ == "__main__":
    # ✅ Set policy again inside the entry point so Uvicorn reload workers inherit it
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            print("[main] Reinforced WindowsSelectorEventLoopPolicy for reload workers.")
        except Exception as e:
            print(f"[main] Failed to reset event loop policy: {e}")

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
