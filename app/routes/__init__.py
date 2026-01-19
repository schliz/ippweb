"""Routes package for ippweb."""

from .print import bp as print_bp
from .auth import auth_bp
from .jobs import jobs_bp

__all__ = ["print_bp", "auth_bp", "jobs_bp"]
