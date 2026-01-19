"""OIDC authentication module for ippweb."""

from functools import wraps
from flask import current_app, g, redirect, request, session, url_for
from authlib.integrations.flask_client import OAuth

from .models import User, db

oauth = OAuth()


def init_oauth(app):
    """Initialize OAuth with the Flask application.
    
    Args:
        app: Flask application instance.
    """
    oauth.init_app(app)
    
    # Register Keycloak as the OIDC provider
    # PKCE is required by Keycloak 26+
    oauth.register(
        name="keycloak",
        client_id=app.config["OIDC_CLIENT_ID"],
        client_secret=app.config["OIDC_CLIENT_SECRET"],
        server_metadata_url=f"{app.config['OIDC_ISSUER']}/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
        },
    )


def get_current_user() -> User | None:
    """Get the current authenticated user from the session.
    
    Returns:
        The User instance if authenticated, None otherwise.
    """
    if hasattr(g, "_current_user"):
        return g._current_user
    
    user_id = session.get("user_id")
    if user_id is None:
        g._current_user = None
        return None
    
    user = db.session.get(User, user_id)
    g._current_user = user
    return user


def login_required(f):
    """Decorator to require authentication for a route.
    
    If the user is not authenticated, they will be redirected to the login page.
    After login, they will be redirected back to the original URL.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if user is None:
            # Store the original URL to redirect back after login
            session["next_url"] = request.url
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function
