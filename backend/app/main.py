"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import auth, bp, cloudflare, hosts, inbounds, nodes, outbounds, panels, servers, tunnel


def create_app() -> FastAPI:
    app = FastAPI(title="pasarguard-tools API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.include_router(auth.router)
    app.include_router(servers.router)
    app.include_router(panels.router)
    app.include_router(nodes.router)
    app.include_router(cloudflare.router)
    app.include_router(inbounds.router)
    app.include_router(outbounds.router)
    app.include_router(hosts.router)
    app.include_router(tunnel.router)
    app.include_router(bp.router)
    return app


app = create_app()
