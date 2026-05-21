"""POST /summarize — 1-3 sentence summary via Claude Haiku.

The Anthropic call is wrapped so model-server failures translate to
typed HTTP responses (Rule 11): 503 for auth/unreachable, 429 for rate
limit, 504 for timeout, 502 for bad upstream response.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.infra import anthropic_client
from model_server.prompts import load_system_user, render

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "summarizer.md"

router = APIRouter()


class SummarizeRequest(BaseModel):
    title: str = Field(default="", description="Issue title")
    body: str = Field(default="", description="Issue body")
    comments: str | None = Field(
        default=None,
        description="Optional comments excerpt; trimmed to a short window upstream.",
    )


class SummarizeResponse(BaseModel):
    summary: str


def _build_user_message(req: SummarizeRequest) -> str:
    _, user_template = load_system_user(PROMPT_PATH)
    comments_section = (
        f"Comments excerpt:\n{req.comments}\n" if req.comments else ""
    )
    return render(
        user_template,
        title=req.title,
        body=req.body,
        comments_section=comments_section,
    )


@router.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest) -> SummarizeResponse:
    system_prompt, _ = load_system_user(PROMPT_PATH)
    user_message = _build_user_message(req)
    try:
        text = anthropic_client.complete(system=system_prompt, user=user_message)
    except anthropic_client.AnthropicAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"anthropic api key not configured: {exc}",
        ) from exc
    except anthropic_client.AnthropicRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except anthropic_client.AnthropicTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except anthropic_client.AnthropicUnreachableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except anthropic_client.AnthropicBadRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except anthropic_client.AnthropicError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return SummarizeResponse(summary=text.strip())
