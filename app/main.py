from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    Dependency bootstrap (Vault -> DB -> Redis -> MinIO -> tracing) and the
    refuse-to-boot contract are wired here in later tasks. For now this is an
    intentionally empty startup/shutdown so the app can serve with no
    dependencies attached.
    """
    yield


app = FastAPI(title="Maintainer's Copilot", lifespan=lifespan)
