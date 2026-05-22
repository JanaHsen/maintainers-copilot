"""Aggregate every resource router into one APIRouter (wired day one — Rule 1)."""

from fastapi import APIRouter

from app.api.routers import auth, health, ner, retrieve, summarize

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(retrieve.router)
api_router.include_router(auth.router)
api_router.include_router(ner.router)
api_router.include_router(summarize.router)
