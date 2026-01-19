"""Print routes blueprint for ippweb."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import magic
import shortuuid
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pypdf import PdfReader

from ..auth import login_required, get_current_user
from ..cups_client import (
    CupsClient,
    CupsError,
    JobNotFoundError,
    PrinterNotFoundError,
)
from ..models import PrintJob, JobStatus, ColorMode, db
from ..services.job_sync import job_sync_service

logger = logging.getLogger(__name__)

bp = Blueprint("print", __name__)


def get_cups_client() -> CupsClient:
    """Get a CUPS client instance."""
    server = current_app.config.get("CUPS_SERVER")
    return CupsClient(server=server)


def allowed_file(filename: str) -> bool:
    """Check if filename has an allowed extension."""
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def validate_pdf(file_path: Path) -> bool:
    """Validate that file is actually a PDF using magic bytes."""
    try:
        mime = magic.from_file(str(file_path), mime=True)
        return mime in current_app.config["ALLOWED_MIMETYPES"]
    except Exception:
        return False


def get_pdf_page_count(file_path: Path) -> int:
    """Extract page count from a PDF file.
    
    Args:
        file_path: Path to the PDF file.
        
    Returns:
        Number of pages in the PDF, or 0 if extraction fails.
    """
    try:
        reader = PdfReader(str(file_path))
        return len(reader.pages)
    except Exception as e:
        logger.warning(f"Failed to extract page count from PDF: {e}")
        return 0


def detect_color_mode(options: dict[str, Any]) -> ColorMode:
    """Detect color mode from print options.
    
    Checks various PPD option names to determine if the print job
    is color (RGB) or grayscale.
    
    Args:
        options: Dictionary of print options from the form.
        
    Returns:
        ColorMode.RGB or ColorMode.GRAY.
    """
    # Map of option names to their value mappings
    color_indicators = {
        "ColorModel": {
            "RGB": ColorMode.RGB,
            "CMYK": ColorMode.RGB,
            "CMY": ColorMode.RGB,
            "Color": ColorMode.RGB,
            "Gray": ColorMode.GRAY,
            "Grayscale": ColorMode.GRAY,
            "Black": ColorMode.GRAY,
        },
        "print-color-mode": {
            "color": ColorMode.RGB,
            "monochrome": ColorMode.GRAY,
            "auto": ColorMode.RGB,  # Assume color for auto
        },
        "output-mode": {
            "color": ColorMode.RGB,
            "grayscale": ColorMode.GRAY,
        },
        "HPColorMode": {
            "ColorPrint": ColorMode.RGB,
            "GrayscalePrint": ColorMode.GRAY,
        },
        "CNColorMode": {
            "color": ColorMode.RGB,
            "mono": ColorMode.GRAY,
        },
    }
    
    for option_name, value_map in color_indicators.items():
        if option_name in options:
            value = options[option_name]
            if value in value_map:
                return value_map[value]
    
    # Fallback: assume color (conservative for billing)
    return ColorMode.RGB


@bp.route("/")
@login_required
def index():
    """Home page - list all printers."""
    user = get_current_user()
    try:
        client = get_cups_client()
        printers = client.get_printers()
    except CupsError as e:
        flash(f"Error connecting to CUPS: {e}", "error")
        printers = []
    
    return render_template("index.html", printers=printers, user=user)


@bp.route("/print/<printer_name>", methods=["GET", "POST"])
@login_required
def print_form(printer_name: str):
    """Display print options form and handle job submission."""
    user = get_current_user()
    client = get_cups_client()
    
    # Get printer info
    try:
        printer = client.get_printer(printer_name)
    except PrinterNotFoundError:
        flash(f"Printer '{printer_name}' not found", "error")
        return redirect(url_for("print.index"))
    except CupsError as e:
        flash(f"Error: {e}", "error")
        return redirect(url_for("print.index"))
    
    # Handle form submission
    if request.method == "POST":
        return handle_print_submission(client, printer_name, user)
    
    # GET - display form with options
    try:
        option_groups = client.get_printer_options(printer_name)
    except CupsError as e:
        flash(f"Error getting printer options: {e}", "error")
        option_groups = []
    
    return render_template(
        "print.html",
        printer=printer,
        option_groups=option_groups,
        user=user,
    )


def handle_print_submission(client: CupsClient, printer_name: str, user):
    """Handle print job submission from form."""
    import uuid
    
    # Check if file was uploaded
    if "file" not in request.files:
        flash("No file selected", "error")
        return redirect(url_for("print.print_form", printer_name=printer_name))
    
    file = request.files["file"]
    
    if file.filename == "" or file.filename is None:
        flash("No file selected", "error")
        return redirect(url_for("print.print_form", printer_name=printer_name))
    
    if not allowed_file(file.filename):
        flash("Only PDF files are allowed", "error")
        return redirect(url_for("print.print_form", printer_name=printer_name))
    
    # Save file with unique name
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    unique_name = f"{uuid.uuid4()}_{file.filename}"
    file_path = upload_folder / unique_name
    
    try:
        file.save(file_path)
        
        # Validate it's actually a PDF
        if not validate_pdf(file_path):
            file_path.unlink(missing_ok=True)
            flash("Invalid PDF file", "error")
            return redirect(url_for("print.print_form", printer_name=printer_name))
        
        # Extract page count from PDF
        page_count = get_pdf_page_count(file_path)
        
        # Collect print options from form
        options = {}
        for key, value in request.form.items():
            # Skip empty values and non-option fields
            if key not in ("file",) and value:
                options[key] = value
        
        # Detect color mode from options
        color_mode = detect_color_mode(options)
        
        # Generate our custom job ID
        job_id = shortuuid.uuid()[:8]  # 8 character short UUID
        now = datetime.now(timezone.utc)
        
        # Create job record in database
        job = PrintJob(
            id=job_id,
            user_id=user.id,
            printer_name=printer_name,
            filename=file.filename,
            page_count=page_count,
            pages_printed=0,
            color_mode=color_mode,
            status=JobStatus.PENDING,
            status_message="Submitting to printer...",
            created_at=now,
            updated_at=now,
        )
        db.session.add(job)
        db.session.commit()
        
        try:
            # Submit print job to CUPS
            cups_job_id = client.submit_job(
                printer_name=printer_name,
                file_path=file_path,
                options=options,
                title=file.filename,
            )
            
            # Update job with CUPS job ID
            job.cups_job_id = cups_job_id
            job.status_message = "Submitted to printer"
            db.session.commit()
            
            flash(f"Print job submitted successfully", "success")
            return redirect(url_for("print.job_status", job_id=job_id))
            
        except CupsError as e:
            # Mark job as aborted if CUPS submission failed
            job.status = JobStatus.ABORTED
            job.status_message = f"Failed to submit: {e}"
            job.completed_at = now
            db.session.commit()
            
            flash(f"Error submitting print job: {e}", "error")
            return redirect(url_for("print.print_form", printer_name=printer_name))
        
    except Exception as e:
        logger.error(f"Unexpected error during print submission: {e}")
        flash(f"Error submitting print job: {e}", "error")
        return redirect(url_for("print.print_form", printer_name=printer_name))
    finally:
        # Clean up uploaded file after job submission
        # Note: CUPS copies the file, so we can delete it immediately
        file_path.unlink(missing_ok=True)


@bp.route("/job/<job_id>")
@login_required
def job_status(job_id: str):
    """Display job status page.
    
    Args:
        job_id: Our custom short UUID job ID.
    """
    user = get_current_user()
    
    # Find job by our ID, verify ownership
    job = PrintJob.query.filter_by(id=job_id, user_id=user.id).first_or_404()
    
    return render_template("status.html", job=job, user=user)


@bp.route("/api/job/<job_id>")
@login_required
def job_status_api(job_id: str):
    """API endpoint for job status (JSON).
    
    Args:
        job_id: Our custom short UUID job ID.
    """
    user = get_current_user()
    
    # Find job by our ID, verify ownership
    job = PrintJob.query.filter_by(id=job_id, user_id=user.id).first()
    
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    
    return jsonify(job.to_dict())


@bp.route("/job/<job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id: str):
    """Cancel a print job.
    
    Args:
        job_id: Our custom short UUID job ID.
    """
    user = get_current_user()
    
    # Find job by our ID, verify ownership
    job = PrintJob.query.filter_by(id=job_id, user_id=user.id).first_or_404()
    
    # Check if job can be canceled
    if job.is_at_printer():
        flash("Cannot cancel job - it is already at the printer", "error")
        return redirect(url_for("print.job_status", job_id=job_id))
    
    if job.is_terminal():
        flash("Cannot cancel job - it has already finished", "error")
        return redirect(url_for("print.job_status", job_id=job_id))
    
    # Cancel in CUPS
    if job.cups_job_id is not None:
        try:
            client = get_cups_client()
            client.cancel_job(job.cups_job_id)
        except JobNotFoundError:
            pass  # Job already gone from CUPS
        except CupsError as e:
            flash(f"Error cancelling job in CUPS: {e}", "error")
    
    # Update our database
    job.status = JobStatus.CANCELED
    job.status_message = "Canceled by user"
    job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    
    # Notify SSE subscribers
    job_sync_service.notify_subscribers(job)
    
    flash("Job cancelled", "success")
    return redirect(url_for("jobs.jobs_dashboard"))
