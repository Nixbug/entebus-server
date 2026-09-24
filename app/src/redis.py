from valkey import Valkey
from typing import Optional, cast
from valkey.exceptions import LockError
from valkey.lock import Lock
import json
from typing import Any

from app.src import exceptions
from app.src.constants import (
    VALKEY_HOST,
    VALKEY_PORT,
    VALKEY_PASSWORD,
    LOCK_TIMEOUT_SECONDS,
    LOCK_MAX_WAIT_SECONDS,
)

# Shared Valkey client
valkey_client = Valkey(
    host=VALKEY_HOST,
    port=int(VALKEY_PORT),
    password=VALKEY_PASSWORD,
    decode_responses=True,
)


def acquire_lock(
    lock_id: str,
    timeout: int = LOCK_TIMEOUT_SECONDS,
    blocking: bool = True,
    blocking_timeout: int = LOCK_MAX_WAIT_SECONDS,
) -> Lock:
    """
    Acquire a Valkey-based mutex lock for a table or specific row.

    Args:
        lock_id (str): Unique identifier for the lock.
        timeout (int): Lock expiration in seconds (auto-released after this).
        blocking (bool): Whether to block until the lock is acquired.
        blocking_timeout (int): Maximum time (in seconds) to wait for lock acquisition.

    Returns:
        Lock: A Valkey lock object if successfully acquired.

    Raises:
        exceptions.LockAcquireTimeout: If the lock could not be acquired within blocking_timeout.
    """
    lock_name = f"lock:{lock_id}"

    try:
        lock = valkey_client.lock(lock_name, timeout=timeout)
        acquired = lock.acquire(
            blocking=blocking,
            blocking_timeout=blocking_timeout if blocking else None,
        )

    except Exception as e:
        exceptions.handle(e)

    if not acquired:
        raise exceptions.LockAcquireTimeout()
    return lock


def release_lock(lock: Optional[Lock]) -> None:
    """
    Release a previously acquired Valkey lock.

    Args:
        lock (Lock | None): The Valkey lock object to release. Does nothing if None.

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


def queue_push(queue_name: str, data: dict[str, Any]) -> None:
    """
    Push an item to the end of a Valkey queue.

    Args:
        queue_name: Name of the Valkey queue.
        data: Dictionary payload to push. The data is serialized to JSON
            before being stored in Valkey.
    """
    valkey_client.rpush(queue_name, json.dumps(data))


def queue_pop(queue_name: str) -> dict[str, Any] | None:
    """
    Pop and return the next item from a Valkey queue.

    Args:
        queue_name: Name of the Valkey queue.

    Returns:
        The next dictionary payload in the queue, or None if the queue
        is empty.

    Valkey queues follow FIFO (First In, First Out) order, so the first
    item added to the queue is the first item returned.
    """
    item = cast(Optional[str], valkey_client.lpop(queue_name))
    if item is None:
        return None

    return json.loads(item)
