from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "ITR Profile Automation"
    VERSION: str = "v1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_PREFIX: str = "/api/itr"

settings = Settings()
