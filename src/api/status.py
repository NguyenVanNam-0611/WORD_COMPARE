from fastapi import APIRouter, HTTPException
from src.core.jobs import get_job

router = APIRouter(prefix="/status", tags=["Status"])


@router.get("")
def health_check():
    return {"status": "OK"}


@router.get("/job/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = job.get("result") or {}

    return {
        # -------- job info --------
        "job_id": job.get("job_id"),
        "username": job.get("username"),
        "date": job.get("date"),
        "status": job.get("status"),  # pending | processing | done | error

        # -------- file info --------
        "original_file_name": job.get("original_file_name"),
        "modified_file_name": job.get("modified_file_name"),
        "original_folder_name": job.get("original_folder_name"),
        "modified_folder_name": job.get("modified_folder_name"),

        # -------- CORE RESULT cho C# --------
        # C# chỉ nên dùng trường này
        "compare": result.get("compare"),

        # -------- optional: debug / xem lại extract --------
        # Bật khi dev, prod có thể bỏ
        "original_blocks": result.get("original"),
        "modified_blocks": result.get("modified"),

        # -------- system flags --------
        "files_deleted": job.get("files_deleted", False),
        "error": job.get("error"),
    }