from typing import Dict, Optional

_JOBS: Dict[str, dict] = {}


def create_job(
    job_id: str,
    username: str,
    original_path: str,
    modified_path: str,
    date: str,
    original_file_name: str,
    modified_file_name: str,
    original_folder_name: str,
    modified_folder_name: str,
    created_at: str
):
    """
    Tạo job mới.
    result được khởi tạo sẵn để tránh None và dễ update về sau.
    """
    _JOBS[job_id] = {
        # -------- identity --------
        "job_id": job_id,
        "username": username,

        # -------- file paths --------
        "original_path": original_path,
        "modified_path": modified_path,

        # -------- meta --------
        "date": date,
        "created_at": created_at,

        # -------- file info --------
        "original_file_name": original_file_name,
        "modified_file_name": modified_file_name,

        "original_folder_name": original_folder_name,
        "modified_folder_name": modified_folder_name,

        # -------- status --------
        "status": "queued",  # queued | processing | done | error

        # -------- RESULT (QUAN TRỌNG) --------
        # luôn tồn tại để API trả ổn định cho C#
        "result": {
            "original": None,   # blocks từ extract_docx(file before)
            "modified": None,   # blocks từ extract_docx(file after)
            "compare": None     # kết quả compare_documents()
        },

        # -------- system --------
        "error": None,
        "files_deleted": False
    }


def update_job(job_id: str, data: dict):
    """
    Update từng phần của job.
    Ví dụ:
        update_job(job_id, {
            "status": "done",
            "result": {...}
        })
    """
    if job_id in _JOBS:
        _JOBS[job_id].update(data)


def get_job(job_id: str) -> Optional[dict]:
    """
    Lấy thông tin job theo job_id.
    """
    return _JOBS.get(job_id)


def mark_job_files_deleted(job_id: str):
    """
    Đánh dấu file upload đã bị xóa (cleanup).
    """
    if job_id in _JOBS:
        _JOBS[job_id]["files_deleted"] = True