import asyncio
from typing import Callable, Awaitable

from src.core.jobs import get_job, update_job

_queue: asyncio.Queue[str] = asyncio.Queue()


async def enqueue_job(job_id: str):
    await _queue.put(job_id)


async def _worker_loop(process_func: Callable[[dict], Awaitable[None]]):
    print("🔥 Worker loop started")

    while True:
        job_id = await _queue.get()
        try:
            job = get_job(job_id)
            if not job:
                print(f"[QUEUE] Job not found: {job_id}")
                continue

            print(f"[QUEUE] Processing job_id={job_id}")
            update_job(job_id, {"status": "processing"})

            await process_func(job)

        except Exception as e:
            print(f"[QUEUE] Job {job_id} crashed: {e}")
            update_job(job_id, {
                "status": "error",
                "error": str(e)
            })

        finally:
            _queue.task_done()


def start_worker(process_func: Callable[[dict], Awaitable[None]]):
    loop = asyncio.get_event_loop()
    loop.create_task(_worker_loop(process_func))