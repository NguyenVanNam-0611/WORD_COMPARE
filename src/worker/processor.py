from pathlib import Path
import asyncio
import traceback
from src.core.jobs import update_job

from src.services.diff_service import DiffService


async def process_job(job: dict):
    original_path = Path(job["original_path"])
    modified_path = Path(job["modified_path"])

    print("====================================")
    print(f"[PROCESS] job_id={job['job_id']} user={job.get('username')}")
    print(f"[PROCESS] original_path={original_path}")
    print(f"[PROCESS] modified_path={modified_path}")

    try:
        if original_path.suffix.lower() != ".docx" or modified_path.suffix.lower() != ".docx":
            raise ValueError("Input must be .docx")

        if not original_path.exists():
            raise FileNotFoundError(f"Original file not found: {original_path}")

        if not modified_path.exists():
            raise FileNotFoundError(f"Modified file not found: {modified_path}")

        service = DiffService()
        compare_result = service.compare(
            str(original_path),
            str(modified_path),
        )

        print("[PROCESS] Compare done")

        compare_payload = compare_result.get("sections", [])
        total_sections = compare_result.get("total_sections", len(compare_payload))
        total_changes = compare_result.get("total_changes", 0)

        update_job(
            job["job_id"],
            {
                "status": "done",
                "result": {
                    "job_id": job["job_id"],
                    "original_file": str(original_path.name),
                    "modified_file": str(modified_path.name),
                    "original_path": str(original_path),
                    "modified_path": str(modified_path),
                    "summary": {
                        "total_sections": total_sections,
                        "total_changes": total_changes,
                    },
                    "compare": {
                        "sections": compare_payload,
                    },
                },
            },
        )

        print(
            f"[PROCESS] Completed job={job['job_id']} "
            f"sections={total_sections} changes={total_changes}"
        )

    except Exception as e:
        error_message = str(e)
        error_trace = traceback.format_exc()

        print("[ERROR]", error_message)
        print(error_trace)

        update_job(
            job["job_id"],
            {
                "status": "error",
                "error": error_message,
                "traceback": error_trace,
            },
        )

    print("====================================")
    await asyncio.sleep(0.2)