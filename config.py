from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    APP_NAME: str = "String Art API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "*"
    DATABASE_URL: str = ""
    JWT_SECRET_KEY: str = ""
    UPLOAD_DIR: str = "storage"
    
    # Unified Production Storage (Local or S3-compatible cloud)
    S3_MODE: str = "local"  # "local" or "s3"
    S3_ENDPOINT: str = "storage"  # local folder path or cloud URL (AWS, Supabase, Cloudflare R2)
    S3_BUCKET: str = ""
    S3_KEY: str = ""
    S3_SECRET: str = ""
    S3_REGION: str = "us-east-1"
    # Credit Packages & Pricing
    DEFAULT_FREE_CREDITS: int = 10
    RUPEES_PER_ART: int = 1
    SILVER_CREDITS: int = 30
    GOLD_CREDITS: int = 60
    PLATINUM_CREDITS: int = 90

    # Razorpay Payment Gateway
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # SMTP Gmail Configuration for OTP
    SMTP_EMAIL: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587



    @property
    def cors_origin_list(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
