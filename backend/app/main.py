from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .core.http import hub
from .routers import data, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await hub.close()


app = FastAPI(title="QUANTX API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["GET"], allow_headers=["*"])
app.include_router(data.router)
app.include_router(system.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
