from celery import Celery
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Celery app configuration
celery_app = Celery(
    "itr_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.profile_tasks"],
)

# Robust config
celery_app.conf.update(
    task_routes={"app.tasks.*": {"queue": "itr"}},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    worker_concurrency=3,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    task_default_retry_delay=10,
    task_time_limit=600,
    broker_heartbeat=30,
    result_expires=3600,
)

@celery_app.task(bind=True, name="app.debug_task")
def debug_task(self):
    print(f"[debug] Executed: {self.request!r}")
