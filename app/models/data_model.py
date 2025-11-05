from pydantic import BaseModel

class ProcessRequest(BaseModel):
    text: str

class ProcessResponse(BaseModel):
    status: str
    result: str
    time_taken: float
