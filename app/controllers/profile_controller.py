from fastapi import APIRouter, Request, HTTPException
from celery import states

from app.tasks.profile_tasks import fetch_itr_profile_task
from app.core.celery_app import celery_app

router = APIRouter(tags=["ITR Profile Automation"])

@router.post("/process")
async def process_itr_profile(request: Request):
    """
    Trigger Celery background task to fetch ITR profile details.
    """
    try:
        body = await request.json()
        user_id = body.get("userId")
        password = body.get("password")

        if not user_id or not password:
            raise HTTPException(status_code=400, detail="Missing userId or password")

        # Queue task to Celery
        task = fetch_itr_profile_task.delay(user_id, password)

        return {
            "status": "queued",
            "task_id": task.id,
            "message": f"ITR profile automation started for {user_id}."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """
    Get Celery task status and result.
    """
    task_result = celery_app.AsyncResult(task_id)

    if task_result.successful():
        result_payload = task_result.result
    elif task_result.failed():
        error = task_result.result
        message = str(error)
        result_payload = {"status": "error", "message": message}
    elif task_result.state == states.RETRY:
        retry_info = task_result.result
        result_payload = {"status": "retry", "message": str(retry_info)}
    else:
        result_payload = None

    return {
        "task_id": task_id,
        "status": task_result.status,
        "result": result_payload,
    }
