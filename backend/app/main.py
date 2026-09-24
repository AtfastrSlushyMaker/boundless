import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.db.session import engine
from app.services.portrait_jobs import portrait_worker
from app.services.post_turn import post_turn_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    workers = [asyncio.create_task(portrait_worker()), asyncio.create_task(post_turn_worker())]
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker
        await engine.dispose()


app = FastAPI(title="Boundless", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)
app.include_router(router, prefix="/api")
