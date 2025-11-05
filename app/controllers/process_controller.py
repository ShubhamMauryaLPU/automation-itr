from fastapi import APIRouter, Header, HTTPException
from app.models.data_model import ProcessRequest, ProcessResponse
from app.services.processor_service import ProcessorService
from app.core.config import settings
from app.core.logger import get_logger

router = APIRouter(prefix="/process", tags=["Processing"])
logger = get_logger(__name__)

@router.post("/", response_model=ProcessResponse)
async def process_data(request: ProcessRequest, x_api_key: str = Header(None)):
    if x_api_key != settings.API_KEY:
        logger.warning("Unauthorized request received")
        raise HTTPException(status_code=403, detail="Unauthorized")

    logger.info(f"Incoming request: {request.text}")
    result = ProcessorService.process_text(request.text)
    return result
