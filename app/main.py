from fastapi import FastAPI
import asyncio
import sys
from fastapi.middleware.cors import CORSMiddleware
from app.controllers import profile_controller
from app.core.config import settings
from app.core.itr_middleware import ITRMiddleware

# Fix for Playwright on Windows (important for RDP)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        print("[init] Using WindowsSelectorEventLoopPolicy for Playwright.")
    except Exception as e:
        print(f"[init] Failed to set Windows event loop policy: {e}")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Handles ITR profile automation tasks asynchronously using Celery + Playwright."
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to your Vercel domain in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging middleware
app.add_middleware(ITRMiddleware)

@app.get("/health")
def health_check():
    return {"status": "ok", "service": settings.PROJECT_NAME}

# Routers
app.include_router(profile_controller.router, prefix=settings.API_PREFIX)

if __name__ == "__main__":
    import uvicorn

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
