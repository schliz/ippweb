"""Configuration module for ippweb."""

import os
from pathlib import Path


class Config:
    """Base configuration."""
    
    # Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")
    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    
    # File uploads
    UPLOAD_FOLDER = Path(os.environ.get("UPLOAD_FOLDER", "./uploads")).resolve()
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 52428800))  # 50MB
    
    # Allowed file extensions
    # NOTE: Currently only PDF is supported. Future extensions can be added here:
    # - PostScript (.ps)
    # - Images (.png, .jpg, .jpeg, .tiff)
    ALLOWED_EXTENSIONS = {"pdf"}
    ALLOWED_MIMETYPES = {"application/pdf"}
    
    # CUPS configuration
    CUPS_SERVER = os.environ.get("CUPS_SERVER")  # None = localhost
    CUPS_PORT = int(os.environ.get("CUPS_PORT", 631))
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///./ippweb.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # OIDC / Keycloak configuration
    OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "http://localhost:8080/realms/ippweb")
    OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "ippweb-client")
    OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "ippweb-secret")
    
    # Job settings
    JOB_TIMEOUT_MINUTES = int(os.environ.get("JOB_TIMEOUT_MINUTES", 5))
    
    @classmethod
    def init_app(cls, app):
        """Initialize application with this config."""
        # Ensure upload folder exists
        cls.UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
