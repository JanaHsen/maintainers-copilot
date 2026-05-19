"""Redis connection pool and a fast reachability probe.

Redis is the only ephemeral store (Rule 3). The dev compose Redis has no
auth, so no secret is involved here.
"""

from functools import lru_cache

import redis
from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings


class RedisUnreachableError(RuntimeError):
    """Redis could not be reached."""


@lru_cache
def get_client() -> Redis:
    settings = get_settings()
    pool = redis.ConnectionPool(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    return Redis(connection_pool=pool)


def ping() -> None:
    """Probe Redis; raises :class:`RedisUnreachableError` on failure."""
    try:
        get_client().ping()
    except RedisError as exc:
        raise RedisUnreachableError("Redis unreachable") from exc
