from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .core.http import hub
from .agent.engine import agent
from .routers import data, quant, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.boot()
    yield
    await agent.shutdown()
    await hub.close()


app = FastAPI(title="QUANTX API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["*"])
app.include_router(data.router)
app.include_router(system.router)
app.include_router(quant.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
