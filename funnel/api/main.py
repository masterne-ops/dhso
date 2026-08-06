"""FastAPI application entry point for the funnel dashboard."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .auth import install_auth
from .db import init_schema, seed_factor_defs
from .factors import SEED_DEFS
from .routers.funnel import router as funnel_router

STATIC_DIR = Path(__file__).parent.parent
PAGE = STATIC_DIR / "funnel_v2.html"

# Ensure the schema exists as soon as the module is imported, so the app works
# no matter how it is started (uvicorn, TestClient, or an embedded import).
init_schema()
seed_factor_defs(SEED_DEFS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    seed_factor_defs(SEED_DEFS)
    yield


app = FastAPI(title="SO漏斗分析 API", version="1.0.0", lifespan=lifespan)
app.include_router(funnel_router)
AUTH_ON = install_auth(app)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness probe. Sits behind auth too, so deploy checks send credentials."""
    return {"ok": True, "auth": AUTH_ON}


@app.get("/", include_in_schema=False)
def index():
    """Serve the funnel page. no-store keeps browsers off a stale build."""
    return FileResponse(PAGE, media_type="text/html",
                        headers={"Cache-Control": "no-store"})
