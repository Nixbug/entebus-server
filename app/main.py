from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.src.schemas import HealthStatus
from app.src.constants import API_TITLE, API_VERSION

app = FastAPI(title=API_TITLE, version=API_VERSION)

origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/health", tags=["Health Check"], response_model=HealthStatus)
async def health_check():
    return {"status": "OK", "version": API_VERSION}
