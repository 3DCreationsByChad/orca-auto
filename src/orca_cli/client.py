"""HTTP client for OrcaSlicer API."""

import httpx
from typing import Optional


class OrcaClient:
    """HTTP client for communicating with the OrcaSlicer API.
    
    Usage:
        client = OrcaClient("http://localhost:8000")
        health = client.health()
        profiles = client.list_profiles()
    """
    
    def __init__(self, base_url: str):
        """Initialize the client.
        
        Args:
            base_url: Base URL of the OrcaSlicer API (e.g., http://localhost:8000)
        """
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
    
    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        """Make an HTTP request to the API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., /health)
            **kwargs: Additional arguments for httpx
            
        Returns:
            Response JSON as dict or list
            
        Raises:
            ConnectionError: If cannot connect to API
            ValueError: For 4xx client errors
            RuntimeError: For 5xx server errors
        """
        url = f"{self.base_url}{path}"
        
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(method, url, **kwargs)
        except httpx.ConnectError as e:
            raise ConnectionError(f"Cannot connect to API at {self.base_url}") from e
        except httpx.TimeoutException as e:
            raise ConnectionError(f"Request to {url} timed out") from e
        
        if response.status_code >= 500:
            detail = "Server error"
            try:
                detail = response.json().get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(f"API error: {detail}")
        
        if response.status_code >= 400:
            detail = "Request failed"
            try:
                detail = response.json().get("detail", detail)
            except Exception:
                pass
            raise ValueError(detail)
        
        return response.json()
    
    def health(self) -> dict:
        """Check API health status.
        
        Returns:
            dict with status and version info
        """
        return self._request("GET", "/health")
    
    def list_files(self, path: str = "") -> list[dict]:
        """List files and directories.
        
        Args:
            path: Optional subdirectory path (relative to models root)
            
        Returns:
            List of file/directory dicts with name, is_dir, size
        """
        if path:
            encoded_path = "/".join(part for part in path.split("/") if part)
            endpoint = f"/api/files/{encoded_path}"
        else:
            endpoint = "/api/files/"
        
        result = self._request("GET", endpoint)
        
        # Handle both list response and dict with contents
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "contents" in result:
            return result["contents"]
        else:
            return []
    
    def list_profiles(self) -> list[str]:
        """List available slice profiles.
        
        Returns:
            List of profile names
        """
        result = self._request("GET", "/api/profiles")
        # API returns list directly
        return result if isinstance(result, list) else []
    
    def create_slice_job(self, file_path: str, profile: str, copies: int = 1) -> dict:
        """Create a new slice job.
        
        Args:
            file_path: Path to STL file (relative to models root)
            profile: Profile name (e.g., draft, standard, quality)
            copies: Number of copies to print (1-99, default 1)
            
        Returns:
            dict with id (job_id) and status
        """
        result = self._request(
            "POST",
            "/api/slice",
            json={"file_path": file_path, "profile": profile, "copies": copies}
        )
        # Normalize response: API returns 'id', client returns 'job_id' for consistency
        if isinstance(result, dict) and "id" in result:
            result["job_id"] = result["id"]
        return result
    
    def list_jobs(self, limit: int = 20) -> list[dict]:
        """List recent jobs.
        
        Args:
            limit: Maximum number of jobs to return
            
        Returns:
            List of job dicts with normalized keys (job_id, file_path)
        """
        result = self._request("GET", f"/api/jobs/?limit={limit}")
        
        # API returns list directly
        jobs = result if isinstance(result, list) else result.get("jobs", [])
        
        # Normalize keys for CLI consistency
        for job in jobs:
            if "id" in job and "job_id" not in job:
                job["job_id"] = job["id"]
            if "input_path" in job and "file_path" not in job:
                job["file_path"] = job["input_path"]
        
        return jobs
    
    def get_job(self, job_id: str) -> dict:
        """Get a specific job by ID.
        
        Args:
            job_id: Job UUID
            
        Returns:
            Job dict with normalized keys
        """
        result = self._request("GET", f"/api/jobs/{job_id}")
        
        # Normalize keys
        if isinstance(result, dict):
            if "id" in result and "job_id" not in result:
                result["job_id"] = result["id"]
            if "input_path" in result and "file_path" not in result:
                result["file_path"] = result["input_path"]
        
        return result
