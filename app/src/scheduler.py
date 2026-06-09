from datetime import datetime
import time
from queue import Queue
from threading import Thread
from sqlalchemy.orm.session import Session

from app.src.constants import TMZ_SECONDARY
from app.src.db import JOB, SessionLocal
from app.src.redis import acquire_lock, release_lock
from app.src import exceptions

job_queue = Queue()


def get_due_jobs(session: Session):
    return (
        session.query(JOB)
        .filter(JOB.next_trigger_on <= datetime.now(TMZ_SECONDARY))
        .all()
    )


def scheduler():
    lock = None

    try:
        lock = acquire_lock(
            "job_scheduler_master",
            timeout=30,
            blocking=False,
        )
        with SessionLocal() as session:
            due_jobs = get_due_jobs(session)
        for job in due_jobs:
            job_queue.put(job.id)
    except exceptions.LockAcquireTimeout:
        pass

    finally:
        release_lock(lock)


def worker():
    while True:
        job_id = job_queue.get()
        run_job(job_id)
        job_queue.task_done()


def run_job(job_id: int):
    print("Hello world")


if __name__ == "__main__":
    worker_thread = Thread(
        target=worker,
        daemon=True,
    )
    worker_thread.start()

    scheduler()

    time.sleep(60)
