"""Flask application factory for ippweb."""

import os
from flask import Flask
from flask_migrate import Migrate
from dotenv import load_dotenv

from .config import config
from .models import db

migrate = Migrate()


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application.
    
    Args:
        config_name: Configuration name ('development', 'production', 'testing').
                    Defaults to FLASK_ENV or 'development'.
    
    Returns:
        Configured Flask application instance.
    """
    # Load environment variables from .env file
    load_dotenv()
    
    if config_name is None:
        config_name = os.environ.get("FLASK_CONFIG") or os.environ.get("FLASK_ENV", "development")
    
    app = Flask(__name__)
    
    # Load configuration
    app_config = config.get(config_name, config["default"])
    app.config.from_object(app_config)
    app_config.init_app(app)
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    
    # Initialize OIDC authentication
    from .auth import init_oauth
    init_oauth(app)
    
    # Initialize job sync service
    from .services.job_sync import job_sync_service
    job_sync_service.init_app(app)
    
    # Register blueprints
    from .routes import print_bp
    from .routes.auth import auth_bp
    from .routes.jobs import jobs_bp
    
    app.register_blueprint(print_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(jobs_bp)
    
    # Health check endpoint
    @app.route("/health")
    def health():
        return {"status": "ok"}
    
    return app
