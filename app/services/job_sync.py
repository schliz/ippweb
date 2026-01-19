"""Job synchronization service for keeping database in sync with CUPS."""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from flask import Flask

from ..cups_client import CupsClient, CupsError
from ..models import PrintJob, JobStatus, db, map_cups_state

logger = logging.getLogger(__name__)


class JobSyncService:
    """Service for synchronizing print job status with CUPS.
    
    This service runs a background thread that periodically checks the status
    of active jobs in CUPS and updates the database accordingly. It also
    handles job timeouts and notifies SSE subscribers of changes.
    """
    
    def __init__(self):
        self._app: Flask | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._subscribers: dict[int, list[Callable]] = {}  # user_id -> callbacks
        self._lock = threading.Lock()
        self._sync_interval = 2.0  # seconds
    
    def init_app(self, app: Flask) -> None:
        """Initialize the service with a Flask application.
        
        Args:
            app: Flask application instance.
        """
        self._app = app
        
        # Start background thread
        if not app.config.get("TESTING"):
            self.start()
    
    def start(self) -> None:
        """Start the background sync thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()
        logger.info("Job sync service started")
    
    def stop(self) -> None:
        """Stop the background sync thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Job sync service stopped")
    
    def subscribe(self, user_id: int, callback: Callable[[PrintJob], None]) -> Callable:
        """Subscribe to job updates for a specific user.
        
        Args:
            user_id: The user ID to subscribe for.
            callback: Function to call when a job is updated.
            
        Returns:
            An unsubscribe function.
        """
        with self._lock:
            if user_id not in self._subscribers:
                self._subscribers[user_id] = []
            self._subscribers[user_id].append(callback)
        
        def unsubscribe():
            with self._lock:
                if user_id in self._subscribers:
                    try:
                        self._subscribers[user_id].remove(callback)
                        if not self._subscribers[user_id]:
                            del self._subscribers[user_id]
                    except ValueError:
                        pass
        
        return unsubscribe
    
    def notify_subscribers(self, job: PrintJob) -> None:
        """Notify all subscribers of a job update.
        
        Args:
            job: The updated PrintJob instance.
        """
        with self._lock:
            callbacks = self._subscribers.get(job.user_id, [])[:]
        
        for callback in callbacks:
            try:
                callback(job)
            except Exception as e:
                logger.error(f"Error notifying subscriber: {e}")
    
    def sync_job(self, job: PrintJob, cups_client: CupsClient, timeout_minutes: int) -> bool:
        """Synchronize a single job with CUPS.
        
        Args:
            job: The PrintJob to synchronize.
            cups_client: CupsClient instance.
            timeout_minutes: Timeout in minutes.
            
        Returns:
            True if the job was updated, False otherwise.
        """
        # Check if job has timed out
        if job.is_timed_out(timeout_minutes):
            self._handle_timeout(job, cups_client)
            return True
        
        # Skip if no CUPS job ID
        if job.cups_job_id is None:
            return False
        
        # Query CUPS for current status with retries
        cups_status = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                cups_status = cups_client.get_job_status(job.cups_job_id)
                job.cups_unreachable = False
                break
            except CupsError as e:
                if attempt == max_retries - 1:
                    logger.warning(f"CUPS unreachable for job {job.id} after {max_retries} attempts: {e}")
                    if not job.cups_unreachable:
                        job.cups_unreachable = True
                        job.updated_at = datetime.now(timezone.utc)
                        return True
                    return False
                # Wait briefly before retrying
                time.sleep(0.5 * (attempt + 1))
        
        if cups_status is None:
            return False
        
        # Map CUPS state to our status
        new_status = map_cups_state(cups_status.state)
        
        # Check if anything changed
        if new_status == job.status and not job.cups_unreachable:
            return False
        
        job.status = new_status
        job.status_message = cups_status.state_message
        job.updated_at = datetime.now(timezone.utc)
        
        # Handle completion
        if new_status == JobStatus.COMPLETED:
            job.pages_printed = cups_status.impressions_completed or job.page_count
            job.completed_at = datetime.now(timezone.utc)
            logger.info(f"Job {job.id} completed: {job.pages_printed} pages printed")
        
        # Handle failure (capture partial pages)
        elif new_status in (JobStatus.ABORTED, JobStatus.CANCELED):
            job.pages_printed = cups_status.impressions_completed or 0
            job.completed_at = datetime.now(timezone.utc)
            logger.info(f"Job {job.id} {new_status.value}: {job.pages_printed} pages printed")
        
        return True
    
    def _handle_timeout(self, job: PrintJob, cups_client: CupsClient) -> None:
        """Handle a job that has exceeded the timeout.
        
        Args:
            job: The timed out PrintJob.
            cups_client: CupsClient instance.
        """
        logger.warning(f"Job {job.id} timed out")
        
        if job.is_at_printer():
            # Job is at printer - do NOT cancel, just mark as timed out
            # This preserves page counting integrity
            job.status = JobStatus.TIMED_OUT
            job.status_message = "Job timed out but may have printed"
            logger.info(f"Job {job.id} timed out at printer, not canceling")
        else:
            # Job is still in queue - safe to cancel
            if job.cups_job_id is not None:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        cups_client.cancel_job(job.cups_job_id)
                        job.status = JobStatus.TIMED_OUT
                        job.status_message = "Job timed out and was canceled"
                        job.pages_printed = 0
                        logger.info(f"Job {job.id} timed out and canceled")
                        break
                    except CupsError as e:
                        if attempt == max_retries - 1:
                            job.status = JobStatus.TIMED_OUT
                            job.status_message = f"Job timed out (cancel failed: {e})"
                            logger.error(f"Failed to cancel timed out job {job.id}: {e}")
                        else:
                            time.sleep(0.5 * (attempt + 1))
            else:
                job.status = JobStatus.TIMED_OUT
                job.status_message = "Job timed out (no CUPS job ID)"
                job.pages_printed = 0
        
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
    
    def _sync_loop(self) -> None:
        """Background thread loop for syncing jobs."""
        while not self._stop_event.is_set():
            try:
                self._sync_all_jobs()
            except Exception as e:
                logger.error(f"Error in sync loop: {e}")
            
            # Wait for next sync interval or stop event
            self._stop_event.wait(timeout=self._sync_interval)
    
    def _sync_all_jobs(self) -> None:
        """Sync all active jobs with CUPS."""
        if self._app is None:
            return
        
        with self._app.app_context():
            # Get all active jobs
            active_jobs = PrintJob.query.filter(
                PrintJob.status.in_(JobStatus.active_states())
            ).all()
            
            if not active_jobs:
                return
            
            cups_client = CupsClient()
            timeout_minutes = self._app.config.get("JOB_TIMEOUT_MINUTES", 5)
            
            for job in active_jobs:
                try:
                    updated = self.sync_job(job, cups_client, timeout_minutes)
                    if updated:
                        db.session.commit()
                        self.notify_subscribers(job)
                except Exception as e:
                    logger.error(f"Error syncing job {job.id}: {e}")
                    db.session.rollback()
    
    def sync_user_jobs(self, user_id: int) -> list[PrintJob]:
        """Sync all active jobs for a specific user.
        
        This is called when a user opens the SSE stream for immediate updates.
        
        Args:
            user_id: The user ID to sync jobs for.
            
        Returns:
            List of active jobs for the user.
        """
        from flask import current_app
        
        # Get all active jobs for this user
        active_jobs = PrintJob.query.filter(
            PrintJob.user_id == user_id,
            PrintJob.status.in_(JobStatus.active_states())
        ).all()
        
        if not active_jobs:
            return []
        
        cups_client = CupsClient()
        timeout_minutes = current_app.config.get("JOB_TIMEOUT_MINUTES", 5)
        
        for job in active_jobs:
            try:
                updated = self.sync_job(job, cups_client, timeout_minutes)
                if updated:
                    db.session.commit()
            except Exception as e:
                logger.error(f"Error syncing job {job.id}: {e}")
                db.session.rollback()
        
        return active_jobs


# Global instance
job_sync_service = JobSyncService()
