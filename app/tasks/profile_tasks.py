import asyncio
from app.core.celery_app import celery_app
from app.services.itr_service import fetch_itr_profile
from app.core.logger import get_logger

logger = get_logger(__name__)

@celery_app.task(name="app.tasks.profile_tasks.fetch_itr_profile_task", bind=True, max_retries=3)
def fetch_itr_profile_task(self, user_id: str, password: str):
    """
    Background Celery task to fetch ITR profile details asynchronously.
    Safely runs async Playwright logic inside a sync Celery worker.
    """
    loop = asyncio.new_event_loop()
    try:
        logger.info(f"[task] Starting ITR profile fetch for user: {user_id}")

        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(fetch_itr_profile(user_id, password))

        logger.info(f"[task] Completed ITR profile fetch for user: {user_id}")
        return {"status": "success", "data": result}

    except Exception as e:
        logger.error(f"[task] Error while fetching for {user_id}: {str(e)}")

        # Retry mechanism — retries up to max_retries
        try:
            self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.critical(f"[task] Max retries exceeded for user: {user_id}")

        return {"status": "error", "message": str(e)}
    finally:
        asyncio.set_event_loop(None)
        loop.close()
