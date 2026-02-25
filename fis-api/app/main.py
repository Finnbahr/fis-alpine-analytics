"""
FIS Alpine Analytics API - Main application
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from app.config import settings
from app.database import test_connection
from app.routers import athletes_raw as athletes, races, search, leaderboards, courses, analytics

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== Event Handlers ==========

@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    logger.info("🚀 Starting FIS Alpine Analytics API")
    logger.info(f"📍 API Version: {settings.API_VERSION}")

    # Test database connection
    if test_connection():
        logger.info("✅ Database connection verified")
    else:
        logger.error("❌ Database connection failed - check configuration")


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    logger.info("👋 Shutting down FIS Alpine Analytics API")


# ========== Root Endpoint ==========

@app.get("/", tags=["Root"])
def root():
    """API root endpoint."""
    return {
        "message": "FIS Alpine Analytics API",
        "version": settings.API_VERSION,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint."""
    db_ok = test_connection()
    return {
        "status": "healthy" if db_ok else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
        "version": settings.API_VERSION
    }


# ========== Include Routers ==========

app.include_router(athletes.router, prefix="/api/v1", tags=["Athletes"])
app.include_router(races.router, prefix="/api/v1", tags=["Races"])
app.include_router(search.router, prefix="/api/v1", tags=["Search"])
app.include_router(leaderboards.router, prefix="/api/v1", tags=["Leaderboards"])
app.include_router(courses.router, prefix="/api/v1", tags=["Courses"])
app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])


# ========== Exception Handlers ==========

@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Handle 404 errors."""
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "NOT_FOUND",
                "message": "Resource not found",
                "details": {"path": str(request.url)}
            }
        }
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal server error occurred",
                "details": {}
            }
        }
    )


# ========== Development Server ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True  # Auto-reload on code changes
    )
