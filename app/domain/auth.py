"""Pydantic schemas + role literal for authenticated maintainers.

Aligned with contracts/auth.openapi.yaml. fastapi-users consumes UserRead /
UserCreate / UserUpdate as its schema set; ``role`` extends the base shape
with the application-level scope distinction (``user`` vs ``admin``).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi_users import schemas

Role = Literal["user", "admin"]


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role = "user"


class UserCreate(schemas.BaseUserCreate):
    role: Role = "user"


class UserUpdate(schemas.BaseUserUpdate):
    role: Role | None = None
