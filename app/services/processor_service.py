import time
from app.core.logger import get_logger

logger = get_logger(__name__)

class ProcessorService:
    @staticmethod
    def process_text(text: str) -> dict:
        logger.info(f"Processing started for text: {text}")
        start_time = time.time()
        time.sleep(2)
        processed_result = text.upper()
        duration = round(time.time() - start_time, 2)
        logger.info(f"Processing completed in {duration}s")
        return {
            "status": "success",
            "result": processed_result,
            "time_taken": duration
        }
