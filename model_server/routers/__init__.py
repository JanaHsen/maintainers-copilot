from fastapi import APIRouter

from model_server.routers import classify, ner, summarize

api_router = APIRouter()
api_router.include_router(classify.router)
api_router.include_router(ner.router)
api_router.include_router(summarize.router)
