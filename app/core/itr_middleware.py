import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import os

# Setup logs
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/itr_requests.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

class ITRMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_ip = request.client.host
        path = request.url.path

        logging.info(f"Incoming request: {path} from {client_ip}")

        try:
            response = await call_next(request)
        except Exception as e:
            logging.error(f"Error processing {path}: {e}")
            raise e

        process_time = round(time.time() - start_time, 3)
        logging.info(f"Completed {path} in {process_time}s")

        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Service-Name"] = "ITR Profile Automation"
        return response
