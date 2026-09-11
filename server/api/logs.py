"""
Log Viewer API - View server logs through web browser
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
import os
import asyncio
from typing import Optional

from api.auth import require_admin
from models.user import User

router = APIRouter(prefix="/api/logs", tags=["Logs"])

LOG_FILE = "/tmp/uvicorn.log"


@router.get("/", response_class=PlainTextResponse)
def get_logs(
    lines: int = Query(default=100, ge=1, le=5000),
    current_user: User = Depends(require_admin),
):
    """Get last N lines of server logs"""
    
    if not os.path.exists(LOG_FILE):
        return "Log file not found. Server may be running with different log configuration."
    
    try:
        with open(LOG_FILE, 'r') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:]
            return "".join(last_lines)
    except Exception as e:
        return f"Error reading logs: {e}"


@router.get("/tail", response_class=PlainTextResponse)
def tail_logs(
    lines: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(require_admin),
):
    """Get last N lines (compact view)"""
    
    if not os.path.exists(LOG_FILE):
        return "Log file not found"
    
    try:
        # Use tail command for efficiency
        import subprocess
        result = subprocess.run(
            ["tail", "-n", str(lines), LOG_FILE],
            capture_output=True,
            text=True
        )
        return result.stdout or "No logs yet"
    except Exception as e:
        return f"Error: {e}"


@router.get("/errors", response_class=PlainTextResponse)
def get_error_logs(
    lines: int = Query(default=100, ge=1, le=1000),
    current_user: User = Depends(require_admin),
):
    """Get only error lines from logs"""
    
    if not os.path.exists(LOG_FILE):
        return "Log file not found"
    
    try:
        import subprocess
        result = subprocess.run(
            f"grep -i 'error\\|exception\\|traceback\\|failed' {LOG_FILE} | tail -n {lines}",
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout or "No errors found! 🎉"
    except Exception as e:
        return f"Error: {e}"


@router.get("/requests", response_class=PlainTextResponse)  
def get_request_logs(
    lines: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(require_admin),
):
    """Get only HTTP request logs"""
    
    if not os.path.exists(LOG_FILE):
        return "Log file not found"
    
    try:
        import subprocess
        result = subprocess.run(
            f"grep 'HTTP' {LOG_FILE} | tail -n {lines}",
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout or "No requests logged yet"
    except Exception as e:
        return f"Error: {e}"


@router.get("/info")
def log_info(current_user: User = Depends(require_admin)):
    """Get log file information"""
    
    if not os.path.exists(LOG_FILE):
        return {"exists": False, "path": LOG_FILE}
    
    try:
        stat = os.stat(LOG_FILE)
        with open(LOG_FILE, 'r') as f:
            line_count = sum(1 for _ in f)
        
        return {
            "exists": True,
            "path": LOG_FILE,
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 2),
            "line_count": line_count,
            "endpoints": {
                "all_logs": "/api/logs/?lines=100",
                "tail": "/api/logs/tail?lines=50",
                "errors_only": "/api/logs/errors",
                "requests_only": "/api/logs/requests"
            }
        }
    except Exception as e:
        return {"error": str(e)}


@router.delete("/clear")
def clear_logs(current_user: User = Depends(require_admin)):
    """Clear the log file"""
    
    if not os.path.exists(LOG_FILE):
        return {"cleared": False, "message": "Log file not found"}
    
    try:
        with open(LOG_FILE, 'w') as f:
            f.write("")
        return {"cleared": True, "message": "Log file cleared"}
    except Exception as e:
        return {"cleared": False, "error": str(e)}
