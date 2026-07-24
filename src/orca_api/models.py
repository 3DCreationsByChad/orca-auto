"""Pydantic models for API request/response schemas."""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    """Basic file information for directory listings."""
    
    name: str = Field(description="File or directory name")
    path: str = Field(description="Relative path from models root")
    is_dir: bool = Field(description="True if directory")
    size: int = Field(description="Size in bytes (0 for directories)")
    modified: datetime = Field(description="Last modification time")
    file_count: Optional[int] = Field(
        default=None,
        description="Number of sliceable files (for directories only)"
    )
    
    model_config = {"from_attributes": True}


class FileMetadata(FileInfo):
    """Extended file info with STL metadata."""
    
    vertices: Optional[int] = Field(
        default=None,
        description="Number of vertices in mesh"
    )
    dimensions: Optional[dict[str, float]] = Field(
        default=None,
        description="Bounding box dimensions {x, y, z} in mm"
    )
    volume: Optional[float] = Field(
        default=None,
        description="Mesh volume in cm^3"
    )


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str = "ok"
    version: str
    models_path_accessible: bool
    profiles_path_accessible: bool


class SliceResult(BaseModel):
    """Result of a slicing operation."""

    success: bool = Field(description="Whether slicing succeeded")
    output_path: Optional[str] = Field(
        default=None,
        description="Path to output .gcode.3mf file"
    )
    stdout: str = Field(default="", description="CLI stdout output")
    stderr: str = Field(default="", description="CLI stderr output")
    duration_seconds: float = Field(
        default=0,
        description="Time taken to slice in seconds"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if slicing failed"
    )
    print_time_seconds: Optional[int] = Field(
        default=None,
        description="Estimated print time in seconds (from 3MF metadata)"
    )
    filament_grams: Optional[float] = Field(
        default=None,
        description="Estimated filament usage in grams (from 3MF metadata)"
    )


class ExportResult(BaseModel):
    """Result of a SCAD-to-STL export operation."""

    success: bool = Field(description="Whether export succeeded")
    output_path: Optional[str] = Field(
        default=None,
        description="Path to output .stl file"
    )
    stdout: str = Field(default="", description="OpenSCAD stdout output")
    stderr: str = Field(default="", description="OpenSCAD stderr output")
    duration_seconds: float = Field(
        default=0,
        description="Time taken to export in seconds"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if export failed"
    )


# Job queue models

class JobStatus(str, Enum):
    """Status of a slice job."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    """Slice job with status and metadata."""
    
    id: str = Field(description="Unique job identifier (UUID)")
    input_path: str = Field(description="Path to input STL file")
    profile: str = Field(description="Slicing profile name")
    copies: int = Field(default=1, ge=1, le=99, description="Number of copies to print")
    machine: Optional[str] = Field(
        default=None,
        description="Machine profile name (optional, defaults to A1 Mini)"
    )
    filament: Optional[str] = Field(
        default=None,
        description="Filament profile name (optional, auto-selects based on machine)"
    )
    status: JobStatus = Field(description="Current job status")
    created_at: datetime = Field(description="Job creation time")
    started_at: Optional[datetime] = Field(
        default=None,
        description="When job execution started"
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When job execution completed"
    )
    output_path: Optional[str] = Field(
        default=None,
        description="Path to output .gcode.3mf file"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if job failed"
    )
    print_time_seconds: Optional[int] = Field(
        default=None,
        description="Estimated print time from slicer"
    )
    filament_grams: Optional[float] = Field(
        default=None,
        description="Estimated filament usage in grams"
    )
    
    model_config = {"from_attributes": True}


class JobCreate(BaseModel):
    """Request to create a new slice job."""
    
    file_path: str = Field(description="Relative path to STL file from models root")
    profile: str = Field(description="Profile name (draft, standard, quality, functional)")
    copies: int = Field(default=1, ge=1, le=99, description="Number of copies to print")
    machine: Optional[str] = Field(
        default=None,
        description="Machine profile name (optional, defaults to A1 Mini)"
    )
    filament: Optional[str] = Field(
        default=None,
        description="Filament profile name (optional, auto-selects based on machine)"
    )


class JobResponse(Job):
    """Job response with additional computed fields."""
    pass


class ProfileInfo(BaseModel):
    """Available slicing profile."""

    name: str = Field(description="Profile name")
    description: Optional[str] = Field(default=None, description="Profile description")


# Pipeline models for SCAD-to-printer workflow


class PipelineStatus(str, Enum):
    """Status of a pipeline job through all stages."""
    EDITING = "editing"
    EXPORTING = "exporting"
    SLICING = "slicing"
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineJob(BaseModel):
    """Pipeline job tracking SCAD -> STL -> GCode workflow."""

    id: str = Field(description="Unique pipeline job identifier (UUID)")
    scad_path: str = Field(description="Relative path to SCAD file from models root")
    scad_params: dict = Field(default_factory=dict, description="Parameter overrides for SCAD")
    profile: str = Field(description="Slicing profile name")
    machine: Optional[str] = Field(default=None, description="Machine profile name")
    filament: Optional[str] = Field(default=None, description="Filament profile name")
    pipeline_status: PipelineStatus = Field(
        default=PipelineStatus.EDITING,
        description="Current stage in the pipeline"
    )
    exported_stl_path: Optional[str] = Field(
        default=None,
        description="Path to exported STL file (after EXPORTING stage)"
    )
    slice_job_id: Optional[str] = Field(
        default=None,
        description="ID of the underlying slice job (after SLICING stage)"
    )
    output_path: Optional[str] = Field(
        default=None,
        description="Path to output .gcode.3mf file (after COMPLETED)"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if pipeline failed"
    )
    created_at: datetime = Field(description="Pipeline job creation time")
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When pipeline completed or failed"
    )
    print_time_seconds: Optional[int] = Field(
        default=None,
        description="Estimated print time from slicer"
    )
    filament_grams: Optional[float] = Field(
        default=None,
        description="Estimated filament usage in grams"
    )

    model_config = {"from_attributes": True}


class PipelineJobCreate(BaseModel):
    """Request to create a new pipeline job."""

    scad_path: str = Field(description="Relative path to SCAD file from models root")
    params: dict = Field(default_factory=dict, description="Parameter overrides for SCAD")
    profile: str = Field(description="Profile name (draft, standard, quality, functional)")
    machine: Optional[str] = Field(
        default=None,
        description="Machine profile name (optional, defaults to A1 Mini)"
    )
    filament: Optional[str] = Field(
        default=None,
        description="Filament profile name (optional, auto-selects based on machine)"
    )


# SCAD parameter editing models

class ScadVariableType(str, Enum):
    """Type of an OpenSCAD customizer variable."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    VECTOR = "vector"


class ScadVariable(BaseModel):
    """A customizable variable extracted from an OpenSCAD file."""

    name: str = Field(description="Variable name (e.g., 'text', 'height')")
    type: ScadVariableType = Field(description="Variable type")
    value: str | float | bool | list = Field(
        description="Current value of the variable"
    )
    default_value: str | float | bool | list = Field(
        description="Original value from the file"
    )
    description: Optional[str] = Field(
        default=None,
        description="Description from comment (e.g., 'Custom text to display')"
    )
    min_value: Optional[float] = Field(
        default=None,
        description="Minimum value from [min:max] annotation"
    )
    max_value: Optional[float] = Field(
        default=None,
        description="Maximum value from [min:max] annotation"
    )
    step: Optional[float] = Field(
        default=None,
        description="Step value from [min:step:max] annotation"
    )
    options: Optional[list] = Field(
        default=None,
        description="Dropdown options from [opt1, opt2, opt3] annotation"
    )
    line_number: int = Field(
        description="Line number in file (1-indexed) for replacement"
    )

    model_config = {"from_attributes": True}


class ScadFile(BaseModel):
    """An OpenSCAD file with its extracted customizable variables."""

    path: str = Field(description="Relative path from models_path")
    name: str = Field(description="Filename (e.g., 'nameplate.scad')")
    variables: list[ScadVariable] = Field(
        default_factory=list,
        description="List of customizable variables"
    )
    raw_content: str = Field(
        description="Original file content for modification"
    )

    model_config = {"from_attributes": True}
