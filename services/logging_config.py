"""
Centralized logging configuration for the backend.
Provides structured logging with context propagation for request tracing.
"""

import logging
import sys
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Optional

# Context variables for request tracing
request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar('user_id', default=None)
session_id_var: ContextVar[Optional[str]] = ContextVar('session_id', default=None)
job_id_var: ContextVar[Optional[str]] = ContextVar('job_id', default=None)


class StructuredFormatter(logging.Formatter):
    """
    Formatter that outputs structured key=value logs for easy parsing.
    Includes context variables (request_id, user_id, session_id, job_id) when set.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Start with timestamp, level, and logger name
        parts = [
            self.formatTime(record, self.datefmt),
            record.levelname,
            record.name + ":",
            record.getMessage()
        ]
        
        # Add context variables if set
        context_parts = []
        
        request_id = request_id_var.get()
        if request_id:
            context_parts.append(f"request_id={request_id}")
        
        job_id = job_id_var.get()
        if job_id:
            context_parts.append(f"job_id={job_id}")
        
        user_id = user_id_var.get()
        if user_id:
            context_parts.append(f"user_id={user_id}")
        
        session_id = session_id_var.get()
        if session_id:
            context_parts.append(f"session_id={session_id}")
        
        # Add any extra context passed to the log call
        if hasattr(record, 'extra_context') and record.extra_context:
            for key, value in record.extra_context.items():
                context_parts.append(f"{key}={value}")
        
        # Combine message with context
        base_message = " ".join(parts)
        if context_parts:
            return f"{base_message} | {' '.join(context_parts)}"
        return base_message


class ContextLogger(logging.LoggerAdapter):
    """
    Logger adapter that allows passing extra context as keyword arguments.
    
    Usage:
        logger = get_logger(__name__)
        logger.info("Processing started", duration_ms=150, status="success")
    """
    
    def process(self, msg: str, kwargs: dict) -> tuple:
        # Extract extra context from kwargs
        extra = kwargs.get('extra', {})
        
        # Move non-standard kwargs to extra_context
        extra_context = {}
        standard_keys = {'exc_info', 'stack_info', 'stacklevel', 'extra'}
        for key in list(kwargs.keys()):
            if key not in standard_keys:
                extra_context[key] = kwargs.pop(key)
        
        if extra_context:
            extra['extra_context'] = extra_context
        
        kwargs['extra'] = extra
        return msg, kwargs


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure logging for the application with structured output.
    Call this once at application startup.
    
    Args:
        level: The logging level (default: INFO)
    """
    # Create formatter
    formatter = StructuredFormatter(
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add stdout handler with structured formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Suppress verbose third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def get_logger(name: str) -> ContextLogger:
    """
    Get a context-aware logger for a module.
    
    Args:
        name: Usually __name__ of the calling module
        
    Returns:
        ContextLogger that supports extra keyword arguments
    
    Usage:
        logger = get_logger(__name__)
        logger.info("Voice clone started", provider="inworld", audio_bytes=52480)
    """
    return ContextLogger(logging.getLogger(name), {})


def set_request_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    Set request context for logging. Returns the request_id.
    
    Args:
        request_id: Unique request identifier (generated if not provided)
        user_id: User ID from auth or request body
        session_id: Session ID from request
        
    Returns:
        The request_id (generated or provided)
    """
    req_id = request_id or str(uuid.uuid4())[:8]
    request_id_var.set(req_id)
    
    if user_id:
        user_id_var.set(user_id)
    if session_id:
        session_id_var.set(session_id)
    
    return req_id


def set_job_context(
    job_id: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """
    Set job context for worker logging.
    
    Args:
        job_id: The job ID being processed
        user_id: User ID associated with the job
        session_id: Session ID associated with the job
    """
    job_id_var.set(job_id)
    
    if user_id:
        user_id_var.set(user_id)
    if session_id:
        session_id_var.set(session_id)


def clear_context() -> None:
    """Clear all logging context variables."""
    request_id_var.set(None)
    user_id_var.set(None)
    session_id_var.set(None)
    job_id_var.set(None)


def log_external_call(
    service_name: str,
    operation: str
) -> Callable:
    """
    Decorator to log external API calls with timing.
    
    Args:
        service_name: Name of the external service (e.g., "Hedra", "Inworld")
        operation: The operation being performed (e.g., "clone_voice", "create_asset")
    
    Usage:
        @log_external_call("Hedra", "create_asset")
        def _upload_to_hedra(self, image_path: str) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            logger = get_logger(func.__module__)
            
            logger.info(
                f"{service_name} API call started",
                service=service_name,
                operation=operation
            )
            
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start_time) * 1000)
                
                logger.info(
                    f"{service_name} API call completed",
                    service=service_name,
                    operation=operation,
                    duration_ms=duration_ms,
                    status="success"
                )
                return result
                
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.error(
                    f"{service_name} API call failed: {e}",
                    service=service_name,
                    operation=operation,
                    duration_ms=duration_ms,
                    status="error",
                    error=str(e)
                )
                raise
        
        return wrapper
    return decorator
