"""Authentication routes for ippweb."""

from flask import Blueprint, current_app, redirect, session, url_for
from ..auth import oauth
from ..models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    """Initiate OIDC login flow by redirecting to Keycloak."""
    redirect_uri = url_for("auth.callback", _external=True)
    return oauth.keycloak.authorize_redirect(redirect_uri)


@auth_bp.route("/callback")
def callback():
    """Handle OIDC callback after user authentication.
    
    This route exchanges the authorization code for tokens,
    extracts user info, creates/updates the user in the database,
    and establishes a session.
    """
    token = oauth.keycloak.authorize_access_token()
    
    # Get user info from ID token
    userinfo = token.get("userinfo")
    if userinfo is None:
        # Fallback: parse ID token claims
        userinfo = oauth.keycloak.parse_id_token(token)
    
    # Create or update user in database
    user = User.upsert_from_oidc(userinfo)
    
    # Store user ID and tokens in session
    session["user_id"] = user.id
    session["id_token"] = token.get("id_token")
    
    # Redirect to the original URL or home
    next_url = session.pop("next_url", None)
    return redirect(next_url or url_for("print.index"))


@auth_bp.route("/logout")
def logout():
    """Log out the user by clearing the session and redirecting to Keycloak logout."""
    id_token = session.get("id_token")
    
    # Clear Flask session
    session.clear()
    
    # Redirect to Keycloak logout endpoint
    if id_token:
        issuer = current_app.config["OIDC_ISSUER"]
        client_id = current_app.config["OIDC_CLIENT_ID"]
        post_logout_redirect = url_for("print.index", _external=True)
        
        # Keycloak logout endpoint
        logout_url = (
            f"{issuer}/protocol/openid-connect/logout"
            f"?id_token_hint={id_token}"
            f"&client_id={client_id}"
            f"&post_logout_redirect_uri={post_logout_redirect}"
        )
        return redirect(logout_url)
    
    # No token, just redirect home
    return redirect(url_for("print.index"))
