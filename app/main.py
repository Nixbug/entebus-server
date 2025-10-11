"""
Main entry point for the EnteBus FastAPI application.

This module:
    - Initializes the FastAPI app with metadata.
    - Configures CORS middleware for cross-origin access.
    - Mounts sub-applications for different domains:
        * Executive API
        * Vendor API
        * Operator API
        * Public API
    - Provides a health check endpoint to verify service status.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.src.schemas import HealthStatus
from app.src.constants import API_TITLE, API_VERSION
from app.api.controller import app_executive, app_operator, app_vendor, app_public
from app.src.urls import URL_HEALTH

app = FastAPI(title=API_TITLE, version=API_VERSION)

# Configure CORS (Cross-Origin Resource Sharing)
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount sub-applications for different API domains
app.mount("/executive", app_executive, "Executive API")
app.mount("/vendor", app_vendor, "Vendor API")
app.mount("/operator", app_operator, "Operator API")
app.mount("/public", app_public, "Public API")


# ---------------------------------------------------------------------------
## Health check endpoint
# ---------------------------------------------------------------------------
@app.get(
    URL_HEALTH,
    tags=["Health Check"],
    response_model=HealthStatus,
)
async def health_check():
    """
    **Perform a basic health check to verify service availability.**
    - This endpoint serves as a lightweight check to confirm that the API is running and responsive.
    - It returns a simple JSON response indicating the current status and version of the API.
    """
    return {"status": "OK", "version": API_VERSION}
