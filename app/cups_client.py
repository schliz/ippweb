"""CUPS client wrapper for ippweb.

This module provides a high-level interface to CUPS operations using pycups.
It handles printer enumeration, option parsing, job submission, and status queries.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cups


# Mapping of technical PPD option keywords to user-friendly labels
FRIENDLY_OPTION_NAMES = {
    "Duplex": "Double-Sided Printing",
    "ColorModel": "Color Mode",
    "HPColorMode": "Color Mode",
    "InputSlot": "Paper Source",
    "MediaType": "Paper Type",
    "PageSize": "Paper Size",
}

# Mapping of specific option choices to user-friendly labels
# Structure: { OptionKeyword: { ChoiceValue: FriendlyLabel } }
FRIENDLY_CHOICE_NAMES = {
    "Duplex": {
        "None": "Off (Single-sided)",
        "DuplexNoTumble": "Long Edge (Standard)",
        "DuplexTumble": "Short Edge (Flip)",
    },
    "ColorModel": {
        "Gray": "Black & White",
        "Grayscale": "Black & White",
        "RGB": "Color",
        "CMYK": "Color",
        "Color": "Color",
    },
    "HPColorMode": {
        "GrayscalePrint": "Black & White",
        "ColorPrint": "Color",
    },
    "InputSlot": {
        "Auto": "Automatic Selection",
        "Manual": "Manual Feed",
    },
    "MediaType": {
        "Plain": "Plain Paper",
        "Glossy": "Glossy Photo Paper",
    }
}


@dataclass
class PrintOption:
    """Represents a single print option from the PPD."""
    
    keyword: str  # Internal name (e.g., "PageSize")
    text: str  # Display name (e.g., "Page Size")
    default: str  # Default value keyword
    choices: list[dict[str, str]] = field(default_factory=list)  # [{"value": "A4", "text": "A4"}]


@dataclass
class OptionGroup:
    """Represents a group of related print options."""
    
    name: str  # Group name (e.g., "General", "Media")
    text: str  # Display name
    options: list[PrintOption] = field(default_factory=list)


@dataclass
class PrinterInfo:
    """Information about a printer."""
    
    name: str
    info: str  # Description
    location: str
    make_and_model: str
    state: int  # 3=idle, 4=printing, 5=stopped
    state_message: str
    is_accepting_jobs: bool
    uri: str
    
    @property
    def state_text(self) -> str:
        """Human-readable state."""
        states = {
            3: "Idle",
            4: "Printing",
            5: "Stopped",
        }
        return states.get(self.state, f"Unknown ({self.state})")
    
    @property
    def is_available(self) -> bool:
        """Check if printer is available for jobs."""
        return self.is_accepting_jobs and self.state != 5


@dataclass 
class JobStatus:
    """Status information for a print job."""
    
    job_id: int
    state: int
    state_reasons: list[str]
    impressions_completed: int
    name: str
    printer: str
    state_message: str = ""
    
    # CUPS job states
    STATE_PENDING = 3
    STATE_HELD = 4
    STATE_PROCESSING = 5
    STATE_STOPPED = 6
    STATE_CANCELED = 7
    STATE_ABORTED = 8
    STATE_COMPLETED = 9
    
    @property
    def state_text(self) -> str:
        """Human-readable job state."""
        states = {
            3: "Pending",
            4: "Held",
            5: "Processing",
            6: "Stopped",
            7: "Canceled",
            8: "Aborted",
            9: "Completed",
        }
        return states.get(self.state, f"Unknown ({self.state})")
    
    @property
    def is_finished(self) -> bool:
        """Check if job has finished (success or failure)."""
        return self.state in (
            self.STATE_CANCELED,
            self.STATE_ABORTED,
            self.STATE_COMPLETED,
        )
    
    @property
    def is_success(self) -> bool:
        """Check if job completed successfully."""
        return self.state == self.STATE_COMPLETED
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "state": self.state,
            "state_text": self.state_text,
            "state_message": self.state_message,
            "state_reasons": self.state_reasons,
            "impressions_completed": self.impressions_completed,
            "name": self.name,
            "printer": self.printer,
            "is_finished": self.is_finished,
            "is_success": self.is_success,
        }


class CupsError(Exception):
    """Base exception for CUPS-related errors."""
    pass


class PrinterNotFoundError(CupsError):
    """Raised when a printer is not found."""
    pass


class JobNotFoundError(CupsError):
    """Raised when a job is not found."""
    pass


class CupsClient:
    """High-level CUPS client wrapper.
    
    This class provides a clean interface to CUPS operations, handling
    connection management and data transformation.
    
    Usage:
        client = CupsClient()
        printers = client.get_printers()
        options = client.get_printer_options("MyPrinter")
        job_id = client.submit_job("MyPrinter", "/path/to/file.pdf", {"copies": "2"})
        status = client.get_job_status(job_id)
    """
    
    def __init__(self, server: str | None = None):
        """Initialize CUPS client.
        
        Args:
            server: CUPS server address (None for localhost).
        """
        self._server = server
        self._connection: cups.Connection | None = None
    
    @property
    def connection(self) -> cups.Connection:
        """Get or create CUPS connection."""
        if self._connection is None:
            try:
                if self._server:
                    cups.setServer(self._server)
                self._connection = cups.Connection()
            except RuntimeError as e:
                raise CupsError(f"Failed to connect to CUPS server: {e}") from e
        return self._connection
    
    # CUPS printer-type bitmask for "rejecting jobs"
    # See: https://www.cups.org/doc/cupspm.html (CUPS_PRINTER_REJECTING = 0x10000)
    CUPS_PRINTER_REJECTING = 0x10000

    def get_printers(self) -> list[PrinterInfo]:
        """Get list of all available printers.
        
        Returns:
            List of PrinterInfo objects.
        """
        try:
            printers_dict = self.connection.getPrinters()
        except cups.IPPError as e:
            raise CupsError(f"Failed to get printers: {e}") from e
        
        printers = []
        for name, attrs in printers_dict.items():
            # Determine if printer is accepting jobs:
            # 1. Try to get explicit attribute from getPrinterAttributes() (most reliable)
            # 2. Fall back to checking the CUPS_PRINTER_REJECTING bit in printer-type
            is_accepting = attrs.get("printer-is-accepting-jobs")
            if is_accepting is None:
                # getPrinters() doesn't include this attribute, try getPrinterAttributes()
                try:
                    detailed_attrs = self.connection.getPrinterAttributes(name)
                    is_accepting = detailed_attrs.get("printer-is-accepting-jobs")
                except cups.IPPError:
                    pass  # Fall through to bitmask check
            
            if is_accepting is None:
                # Final fallback: check printer-type bitmask
                # CUPS_PRINTER_REJECTING (0x10000) is SET when printer rejects jobs
                printer_type = attrs.get("printer-type", 0)
                is_accepting = not (printer_type & self.CUPS_PRINTER_REJECTING)
            
            printers.append(PrinterInfo(
                name=name,
                info=attrs.get("printer-info", ""),
                location=attrs.get("printer-location", ""),
                make_and_model=attrs.get("printer-make-and-model", ""),
                state=attrs.get("printer-state", 0),
                state_message=attrs.get("printer-state-message", ""),
                is_accepting_jobs=bool(is_accepting),
                uri=attrs.get("device-uri", ""),
            ))
        
        return printers
    
    def get_printer(self, name: str) -> PrinterInfo:
        """Get a specific printer by name.
        
        Args:
            name: Printer name.
            
        Returns:
            PrinterInfo object.
            
        Raises:
            PrinterNotFoundError: If printer doesn't exist.
        """
        printers = self.get_printers()
        for printer in printers:
            if printer.name == name:
                return printer
        raise PrinterNotFoundError(f"Printer '{name}' not found")
    
    def get_printer_options(self, printer_name: str) -> list[OptionGroup]:
        """Get available print options for a printer.
        
        This parses the printer's PPD file to extract all configurable options.
        For IPP Everywhere printers, CUPS generates a virtual PPD.
        
        Args:
            printer_name: Name of the printer.
            
        Returns:
            List of OptionGroup objects containing PrintOption objects.
            
        Raises:
            PrinterNotFoundError: If printer doesn't exist.
            CupsError: If PPD cannot be retrieved or parsed.
        """
        # Verify printer exists
        self.get_printer(printer_name)
        
        try:
            ppd_path = self.connection.getPPD(printer_name)
        except cups.IPPError as e:
            raise CupsError(f"Failed to get PPD for '{printer_name}': {e}") from e
        
        if not ppd_path:
            raise CupsError(f"No PPD available for printer '{printer_name}'")
        
        try:
            ppd = cups.PPD(ppd_path)
            ppd.markDefaults()
        except RuntimeError as e:
            raise CupsError(f"Failed to parse PPD: {e}") from e
        finally:
            # Clean up temp PPD file
            Path(ppd_path).unlink(missing_ok=True)
        
        groups = []
        for ppd_group in ppd.optionGroups:
            option_group = OptionGroup(
                name=ppd_group.name,
                text=ppd_group.text,
            )
            
            for ppd_option in ppd_group.options:
                choices = []
                default_value = ""
                
                # Determine friendly name for the option itself
                # ppd_option.text might be None in rare cases? It's typed as str in newer pycups but let's be safe
                raw_text = ppd_option.text or ppd_option.keyword
                option_text = FRIENDLY_OPTION_NAMES.get(ppd_option.keyword, raw_text)
                
                for choice in ppd_option.choices:
                    # Determine friendly text for this specific choice
                    choice_val = choice["choice"]
                    choice_text = choice["text"]
                    
                    if ppd_option.keyword in FRIENDLY_CHOICE_NAMES:
                        choice_text = FRIENDLY_CHOICE_NAMES[ppd_option.keyword].get(choice_val, choice_text)
                        
                    choice_dict = {
                        "value": choice_val,
                        "text": choice_text,
                    }
                    choices.append(choice_dict)
                    
                    # Check if this is the marked (default) choice
                    if choice.get("marked", False):
                        default_value = choice["choice"]
                
                # Ensure option_text is a string, even if lookup failed
                final_text = option_text if option_text else ppd_option.keyword

                option = PrintOption(
                    keyword=ppd_option.keyword,
                    text=final_text,
                    default=default_value,
                    choices=choices,
                )
                option_group.options.append(option)
            
            if option_group.options:
                groups.append(option_group)
        
        return groups
    
    def submit_job(
        self,
        printer_name: str,
        file_path: str | Path,
        options: dict[str, str] | None = None,
        title: str | None = None,
    ) -> int:
        """Submit a print job.
        
        Args:
            printer_name: Name of the target printer.
            file_path: Path to the file to print.
            options: Print options as key-value pairs.
            title: Job title (defaults to filename).
            
        Returns:
            Job ID.
            
        Raises:
            PrinterNotFoundError: If printer doesn't exist.
            CupsError: If job submission fails.
            FileNotFoundError: If file doesn't exist.
        """
        # Verify printer exists
        self.get_printer(printer_name)
        
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if title is None:
            title = file_path.name
        
        if options is None:
            options = {}
        
        try:
            job_id = self.connection.printFile(
                printer_name,
                str(file_path),
                title,
                options,
            )
        except cups.IPPError as e:
            raise CupsError(f"Failed to submit print job: {e}") from e
        
        return job_id
    
    def get_job_status(self, job_id: int) -> JobStatus:
        """Get status of a print job.
        
        Args:
            job_id: The job ID returned by submit_job.
            
        Returns:
            JobStatus object with current job state.
            
        Raises:
            JobNotFoundError: If job doesn't exist.
            CupsError: If status query fails.
        """
        # Attributes we want to retrieve
        requested_attrs = [
            "job-id",
            "job-state",
            "job-state-reasons",
            "job-state-message",
            "job-impressions-completed",
            "job-name",
            "job-printer-uri",
        ]
        
        try:
            attrs = self.connection.getJobAttributes(job_id, requested_attrs)
        except cups.IPPError as e:
            error_str = str(e)
            if "client-error-not-found" in error_str.lower():
                raise JobNotFoundError(f"Job {job_id} not found") from e
            raise CupsError(f"Failed to get job status: {e}") from e
        
        if not attrs:
            raise JobNotFoundError(f"Job {job_id} not found")
        
        # Extract printer name from URI
        printer_uri = attrs.get("job-printer-uri", "")
        printer_name = printer_uri.split("/")[-1] if printer_uri else ""
        
        # Handle state reasons (can be string or list)
        state_reasons = attrs.get("job-state-reasons", [])
        if isinstance(state_reasons, str):
            state_reasons = [state_reasons]
        
        return JobStatus(
            job_id=attrs.get("job-id", job_id),
            state=attrs.get("job-state", 0),
            state_reasons=state_reasons,
            state_message=attrs.get("job-state-message", ""),
            impressions_completed=attrs.get("job-impressions-completed", 0),
            name=attrs.get("job-name", ""),
            printer=printer_name,
        )
    
    def cancel_job(self, job_id: int) -> None:
        """Cancel a print job.
        
        Args:
            job_id: The job ID to cancel.
            
        Raises:
            JobNotFoundError: If job doesn't exist.
            CupsError: If cancellation fails.
        """
        try:
            self.connection.cancelJob(job_id)
        except cups.IPPError as e:
            error_str = str(e)
            if "client-error-not-found" in error_str.lower():
                raise JobNotFoundError(f"Job {job_id} not found") from e
            raise CupsError(f"Failed to cancel job: {e}") from e
