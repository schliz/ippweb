"""Jobs routes for ippweb - job history, SSE streaming, and API endpoints."""

import json
import queue
import logging
from flask import Blueprint, Response, current_app, g, render_template, request, jsonify
from sqlalchemy import func

from ..auth import login_required, get_current_user
from ..models import PrintJob, JobStatus, ColorMode, db
from ..services.job_sync import job_sync_service

logger = logging.getLogger(__name__)

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs")
@login_required
def jobs_dashboard():
    """Render the jobs dashboard page showing job history and stats."""
    user = get_current_user()
    return render_template("jobs.html", user=user)


@jobs_bp.route("/api/jobs")
@login_required
def api_list_jobs():
    """API endpoint to list jobs with pagination and filtering.
    
    Query parameters:
        page: Page number (default: 1)
        per_page: Items per page (default: 20, max: 100)
        status: Filter by status (all, completed, failed, pending)
        color_mode: Filter by color mode (all, rgb, gray)
        start_date: Filter by start date (ISO format)
        end_date: Filter by end date (ISO format)
    
    Returns:
        JSON with paginated job list and metadata.
    """
    user = get_current_user()
    
    # Pagination
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    
    # Build query
    query = PrintJob.query.filter(PrintJob.user_id == user.id)
    
    # Status filter
    status_filter = request.args.get("status", "all")
    if status_filter == "completed":
        query = query.filter(PrintJob.status == JobStatus.COMPLETED)
    elif status_filter == "failed":
        query = query.filter(PrintJob.status.in_([
            JobStatus.CANCELED, JobStatus.ABORTED, JobStatus.TIMED_OUT
        ]))
    elif status_filter == "pending":
        query = query.filter(PrintJob.status.in_(JobStatus.active_states()))
    
    # Color mode filter
    color_filter = request.args.get("color_mode", "all")
    if color_filter == "rgb":
        query = query.filter(PrintJob.color_mode == ColorMode.RGB)
    elif color_filter == "gray":
        query = query.filter(PrintJob.color_mode == ColorMode.GRAY)
    
    # Date range filter
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if start_date:
        query = query.filter(PrintJob.created_at >= start_date)
    if end_date:
        query = query.filter(PrintJob.created_at <= end_date)
    
    # Order by creation date, newest first
    query = query.order_by(PrintJob.created_at.desc())
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        "jobs": [job.to_dict() for job in pagination.items],
        "page": page,
        "per_page": per_page,
        "total": pagination.total,
        "pages": pagination.pages,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
    })


@jobs_bp.route("/api/jobs/stats")
@login_required
def api_job_stats():
    """API endpoint to get job statistics for the current user.
    
    Returns:
        JSON with total jobs, page counts by color mode, etc.
    """
    user = get_current_user()
    
    # Total jobs
    total_jobs = PrintJob.query.filter(PrintJob.user_id == user.id).count()
    
    # Pending jobs
    pending_jobs = PrintJob.query.filter(
        PrintJob.user_id == user.id,
        PrintJob.status.in_(JobStatus.active_states())
    ).count()
    
    # Page counts by color mode (only from completed or partially printed jobs)
    countable_statuses = [
        JobStatus.COMPLETED,
        JobStatus.ABORTED,
        JobStatus.CANCELED,
        JobStatus.TIMED_OUT,
    ]
    
    rgb_pages = db.session.query(func.sum(PrintJob.pages_printed)).filter(
        PrintJob.user_id == user.id,
        PrintJob.color_mode == ColorMode.RGB,
        PrintJob.status.in_(countable_statuses)
    ).scalar() or 0
    
    gray_pages = db.session.query(func.sum(PrintJob.pages_printed)).filter(
        PrintJob.user_id == user.id,
        PrintJob.color_mode == ColorMode.GRAY,
        PrintJob.status.in_(countable_statuses)
    ).scalar() or 0
    
    return jsonify({
        "total_jobs": total_jobs,
        "pending_jobs": pending_jobs,
        "total_pages": rgb_pages + gray_pages,
        "rgb_pages": rgb_pages,
        "gray_pages": gray_pages,
    })


@jobs_bp.route("/api/jobs/stream")
@login_required
def api_jobs_stream():
    """Server-Sent Events endpoint for real-time job updates.
    
    This endpoint streams job status updates for the authenticated user.
    The client should connect to this endpoint using EventSource.
    
    Events:
        job-update: Sent when a job status changes. Data is the job JSON.
        connected: Sent on initial connection with current active jobs.
        error: Sent when an error occurs during initial sync.
    """
    user = get_current_user()
    
    def generate():
        # Create a queue for this subscriber
        update_queue = queue.Queue()
        
        def on_job_update(job: PrintJob):
            """Callback for job updates."""
            try:
                update_queue.put_nowait(job.to_dict())
            except queue.Full:
                pass
        
        # Subscribe to updates
        unsubscribe = job_sync_service.subscribe(user.id, on_job_update)
        
        try:
            # Send initial connection event with current active jobs
            try:
                active_jobs = job_sync_service.sync_user_jobs(user.id)
                initial_data = {
                    "active_jobs": [job.to_dict() for job in active_jobs]
                }
            except Exception as e:
                logger.error(f"Error fetching initial jobs for user {user.id}: {e}")
                initial_data = {
                    "active_jobs": [],
                    "error": "Failed to fetch active jobs"
                }
            yield f"event: connected\ndata: {json.dumps(initial_data)}\n\n"
            
            # Stream updates
            while True:
                try:
                    # Wait for updates with timeout (for keepalive)
                    job_data = update_queue.get(timeout=30)
                    yield f"event: job-update\ndata: {json.dumps(job_data)}\n\n"
                except queue.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        except GeneratorExit:
            # Client disconnected
            logger.debug(f"SSE client disconnected for user {user.id}")
        finally:
            unsubscribe()
    
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Connection": "keep-alive",
        }
    )


@jobs_bp.route("/api/jobs/<job_id>")
@login_required
def api_get_job(job_id: str):
    """API endpoint to get a specific job by ID.
    
    Args:
        job_id: The job's short UUID.
        
    Returns:
        JSON with job details.
    """
    user = get_current_user()
    job = PrintJob.query.filter_by(id=job_id, user_id=user.id).first_or_404()
    return jsonify(job.to_dict())
