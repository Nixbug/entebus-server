from redis import Redis
from typing import Optional
from redis.exceptions import LockError
from redis.lock import Lock

from app.src import exceptions
from app.src.constants import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    LOCK_TIMEOUT_SECONDS,
    LOCK_MAX_WAIT_SECONDS,
)

# Shared Redis client
redis_client = Redis(
    host=REDIS_HOST,
    port=int(REDIS_PORT),
    password=REDIS_PASSWORD,
    decode_responses=True,
)


def acquire_lock(
    lock_id: str,
    timeout: int = LOCK_TIMEOUT_SECONDS,
    blocking_timeout: int = LOCK_MAX_WAIT_SECONDS,
) -> Lock:
    """
    Acquire a Redis-based mutex lock for a table or specific row.

    Args:
        lock_id (str): Unique identifier for the lock.
        timeout (int): Lock expiration in seconds (auto-released after this).
        blocking_timeout (int): Maximum time (in seconds) to wait for lock acquisition.

    Returns:
        Lock: A Redis lock object if successfully acquired.

    Raises:
        exceptions.LockAcquireTimeout: If the lock could not be acquired within blocking_timeout.
    """
    lock_name = f"lock:{lock_id}"

    try:
        lock = redis_client.lock(lock_name, timeout=timeout)
        acquired = lock.acquire(
            blocking=True,
            blocking_timeout=blocking_timeout,
        )

    except Exception as e:
        exceptions.handle(e)

    if not acquired:
        raise exceptions.LockAcquireTimeout()
    return lock


def release_lock(lock: Optional[Lock]) -> None:
    """
    Release a previously acquired Redis lock.

    Args:
        lock (Lock | None): The Redis lock object to release. Does nothing if None.

    Notes:
        - Ensures only the owner can release the lock.
        - Handles LockError if the lock is already released or not owned by the caller.
    """
    if lock and lock.locked() and lock.owned():
        try:
            lock.release()
        except LockError:
            return
        except Exception as e:
            exceptions.handle(e)
