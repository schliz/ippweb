"""Database models for ippweb."""

import enum
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ColorMode(enum.Enum):
    """Color mode for print jobs."""
    RGB = "rgb"
    GRAY = "gray"


class JobStatus(enum.Enum):
    """Status of a print job."""
    PENDING = "pending"         # Submitted, waiting in queue
    HELD = "held"               # Held in queue
    PROCESSING = "processing"   # At the printer, actively printing
    COMPLETED = "completed"     # Successfully printed
    CANCELED = "canceled"       # User canceled
    ABORTED = "aborted"         # System/printer error
    TIMED_OUT = "timed_out"     # Exceeded timeout
    
    @classmethod
    def terminal_states(cls) -> set["JobStatus"]:
        """Return states that indicate a job is finished."""
        return {cls.COMPLETED, cls.CANCELED, cls.ABORTED, cls.TIMED_OUT}
    
    @classmethod
    def active_states(cls) -> set["JobStatus"]:
        """Return states that indicate a job is still active."""
        return {cls.PENDING, cls.HELD, cls.PROCESSING}


# CUPS job state to our JobStatus mapping
CUPS_STATE_MAP = {
    3: JobStatus.PENDING,      # IPP_JOB_PENDING
    4: JobStatus.HELD,         # IPP_JOB_HELD
    5: JobStatus.PROCESSING,   # IPP_JOB_PROCESSING
    6: JobStatus.HELD,         # IPP_JOB_STOPPED (treat as held)
    7: JobStatus.CANCELED,     # IPP_JOB_CANCELED
    8: JobStatus.ABORTED,      # IPP_JOB_ABORTED
    9: JobStatus.COMPLETED,    # IPP_JOB_COMPLETED
}


def map_cups_state(cups_state: int) -> JobStatus:
    """Map a CUPS job state to our JobStatus enum."""
    return CUPS_STATE_MAP.get(cups_state, JobStatus.PENDING)


class User(db.Model):
    """User model for storing OIDC user information."""
    
    __tablename__ = "users"
    
    id = db.Column(db.Integer, primary_key=True)
    sub = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    preferred_username = db.Column(db.String(255))
    first_login = db.Column(db.DateTime, nullable=False)
    last_login = db.Column(db.DateTime, nullable=False)
    
    # Relationship to print jobs
    jobs = db.relationship("PrintJob", back_populates="user", lazy="dynamic")
    
    def __repr__(self) -> str:
        return f"<User {self.preferred_username or self.sub}>"
    
    @classmethod
    def upsert_from_oidc(cls, userinfo: dict) -> "User":
        """Create or update a user from OIDC userinfo claims.
        
        Args:
            userinfo: Dictionary containing OIDC claims (sub, name, email, etc.)
            
        Returns:
            The created or updated User instance.
        """
        now = datetime.now(timezone.utc)
        user = cls.query.filter_by(sub=userinfo["sub"]).first()
        
        if user is None:
            user = cls(
                sub=userinfo["sub"],
                first_login=now,
            )
            db.session.add(user)
        
        # Update claims on every login
        user.name = userinfo.get("name")
        user.email = userinfo.get("email")
        user.preferred_username = userinfo.get("preferred_username")
        user.last_login = now
        
        db.session.commit()
        return user


class PrintJob(db.Model):
    """Print job model for tracking print jobs and page counts."""
    
    __tablename__ = "print_jobs"
    
    # Primary key is our custom short UUID
    id = db.Column(db.String(22), primary_key=True)
    
    # User relationship
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User", back_populates="jobs")
    
    # CUPS job reference (nullable after job completes, as CUPS IDs may be reused)
    cups_job_id = db.Column(db.Integer, nullable=True, index=True)
    
    # Printer information
    printer_name = db.Column(db.String(255), nullable=False)
    
    # File information
    filename = db.Column(db.String(255), nullable=False)
    page_count = db.Column(db.Integer, nullable=False, default=0)
    
    # Print result
    pages_printed = db.Column(db.Integer, nullable=False, default=0)
    color_mode = db.Column(db.Enum(ColorMode), nullable=False, default=ColorMode.RGB)
    
    # Status tracking
    status = db.Column(db.Enum(JobStatus), nullable=False, default=JobStatus.PENDING)
    status_message = db.Column(db.String(500))
    cups_unreachable = db.Column(db.Boolean, nullable=False, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    def __repr__(self) -> str:
        return f"<PrintJob {self.id} [{self.status.value}]>"
    
    def is_terminal(self) -> bool:
        """Check if the job is in a terminal (finished) state."""
        return self.status in JobStatus.terminal_states()
    
    def is_active(self) -> bool:
        """Check if the job is still active (needs sync)."""
        return self.status in JobStatus.active_states()
    
    def is_at_printer(self) -> bool:
        """Check if the job is at the printer (cannot be safely canceled).
        
        Once a job reaches PROCESSING or any terminal state, we should not
        attempt to cancel it as pages may have already been printed.
        """
        return self.status in {
            JobStatus.PROCESSING,
            JobStatus.COMPLETED,
            JobStatus.CANCELED,
            JobStatus.ABORTED,
            JobStatus.TIMED_OUT,
        }
    
    def is_timed_out(self, timeout_minutes: int) -> bool:
        """Check if the job has exceeded the timeout.
        
        Args:
            timeout_minutes: Maximum time in minutes before a job is considered timed out.
            
        Returns:
            True if the job has exceeded the timeout and is not in a terminal state.
        """
        if self.is_terminal():
            return False
        
        now = datetime.now(timezone.utc)
        # Handle timezone-naive datetime from database
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        
        elapsed = (now - created).total_seconds() / 60
        return elapsed > timeout_minutes
    
    def to_dict(self) -> dict:
        """Convert the job to a dictionary for JSON serialization."""
        return {
            "id": self.id,
            "printer_name": self.printer_name,
            "filename": self.filename,
            "page_count": self.page_count,
            "pages_printed": self.pages_printed,
            "color_mode": self.color_mode.value,
            "status": self.status.value,
            "status_message": self.status_message,
            "cups_unreachable": self.cups_unreachable,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
