"""SCAD parser service for extracting and modifying OpenSCAD customizer variables."""

import logging
import re
from pathlib import Path
from typing import Any

from orca_api.config import get_settings
from orca_api.models import ScadFile, ScadVariable, ScadVariableType

logger = logging.getLogger(__name__)

# Regex pattern for OpenSCAD variable assignments
# Matches: variable_name = value; // optional comment
VARIABLE_PATTERN = re.compile(
    r'^(\w+)\s*=\s*(.+?)\s*;(?:\s*//\s*(.*))?$'
)

# Regex pattern for range annotation [min:max] or [min:step:max]
RANGE_PATTERN = re.compile(
    r'\[(-?\d+(?:\.\d+)?):(?:(-?\d+(?:\.\d+)?):)?(-?\d+(?:\.\d+)?)\]'
)

# Regex pattern for dropdown options [opt1, opt2, opt3]
OPTIONS_PATTERN = re.compile(
    r'\[([^\[\]]+(?:,[^\[\]]+)+)\]'
)


def _parse_value(raw_value: str) -> tuple[ScadVariableType, str | float | bool | list]:
    """Parse a raw SCAD value string into typed Python value.

    Args:
        raw_value: The value string from the SCAD file (e.g., '"Hello"', '10', 'true', '[1, 2, 3]')

    Returns:
        Tuple of (type, parsed_value)
    """
    value = raw_value.strip()

    # String: quoted with double or single quotes
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return ScadVariableType.STRING, value[1:-1]

    # Boolean
    if value.lower() == 'true':
        return ScadVariableType.BOOLEAN, True
    if value.lower() == 'false':
        return ScadVariableType.BOOLEAN, False

    # Vector: [x, y, z] or [x, y]
    if value.startswith('[') and value.endswith(']'):
        try:
            # Parse as list of numbers
            inner = value[1:-1]
            elements = [float(e.strip()) for e in inner.split(',')]
            return ScadVariableType.VECTOR, elements
        except ValueError:
            # If parsing fails, treat as string
            return ScadVariableType.STRING, value

    # Number: try to parse as float/int
    try:
        if '.' in value:
            return ScadVariableType.NUMBER, float(value)
        else:
            return ScadVariableType.NUMBER, float(int(value))
    except ValueError:
        # Fallback to string
        return ScadVariableType.STRING, value


def _parse_comment(comment: str | None) -> dict:
    """Parse a comment for description and annotations.

    Args:
        comment: The comment text after // (may be None)

    Returns:
        Dict with 'description', 'min_value', 'max_value', 'step', 'options' keys
    """
    result = {
        'description': None,
        'min_value': None,
        'max_value': None,
        'step': None,
        'options': None
    }

    if not comment:
        return result

    comment = comment.strip()

    # Check for range annotation [min:max] or [min:step:max]
    range_match = RANGE_PATTERN.search(comment)
    if range_match:
        result['min_value'] = float(range_match.group(1))
        if range_match.group(2):  # Has step value
            result['step'] = float(range_match.group(2))
        result['max_value'] = float(range_match.group(3))
        # Remove range from comment for description
        comment = RANGE_PATTERN.sub('', comment).strip()

    # Check for dropdown options [opt1, opt2, opt3]
    # Only if not already matched as range
    if not range_match:
        options_match = OPTIONS_PATTERN.search(comment)
        if options_match:
            options_str = options_match.group(1)
            result['options'] = [opt.strip() for opt in options_str.split(',')]
            # Remove options from comment for description
            comment = OPTIONS_PATTERN.sub('', comment).strip()

    # Remaining comment is description
    if comment:
        result['description'] = comment

    return result


def parse_scad_file(file_path: Path) -> ScadFile:
    """Parse an OpenSCAD file and extract customizable variables.

    Scans for variable assignments in OpenSCAD customizer syntax:
    - text = "Hello";           -> STRING
    - height = 10;              -> NUMBER
    - enabled = true;           -> BOOLEAN
    - dims = [10, 20, 30];      -> VECTOR

    Also extracts from comments:
    - Description: text after //
    - Range: // [min:max] or // [min:step:max]
    - Options: // [opt1, opt2, opt3]

    Args:
        file_path: Path to the .scad file

    Returns:
        ScadFile with extracted variables
    """
    logger.info(f"Parsing SCAD file: {file_path}")

    content = file_path.read_text(encoding='utf-8')
    lines = content.splitlines()
    variables = []

    for line_num, line in enumerate(lines, start=1):
        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('/*'):
            continue

        # Try to match variable assignment
        match = VARIABLE_PATTERN.match(stripped)
        if not match:
            continue

        name = match.group(1)
        raw_value = match.group(2)
        comment = match.group(3)

        # Skip special OpenSCAD variables (start with $)
        if name.startswith('$'):
            continue

        # Parse value and type
        var_type, value = _parse_value(raw_value)

        # Parse comment for annotations
        annotations = _parse_comment(comment)

        variable = ScadVariable(
            name=name,
            type=var_type,
            value=value,
            default_value=value,
            description=annotations['description'],
            min_value=annotations['min_value'],
            max_value=annotations['max_value'],
            step=annotations['step'],
            options=annotations['options'],
            line_number=line_num
        )

        variables.append(variable)
        logger.debug(f"Found variable: {name} ({var_type.value}) = {value}")

    # Get relative path from models_path
    settings = get_settings()
    try:
        relative_path = str(file_path.relative_to(settings.models_path))
    except ValueError:
        relative_path = file_path.name

    scad_file = ScadFile(
        path=relative_path,
        name=file_path.name,
        variables=variables,
        raw_content=content
    )

    logger.info(f"Parsed {len(variables)} variables from {file_path.name}")
    return scad_file


def apply_params(scad_file: ScadFile, params: dict[str, Any]) -> str:
    """Apply parameter changes to SCAD file content.

    Uses line numbers from ScadVariable to locate and replace values,
    preserving original formatting and comments.

    Args:
        scad_file: Parsed ScadFile with variables and raw_content
        params: Dict of {variable_name: new_value}

    Returns:
        Modified SCAD content with updated variable values
    """
    lines = scad_file.raw_content.splitlines()

    # Build lookup of variables by name
    var_by_name = {v.name: v for v in scad_file.variables}

    for var_name, new_value in params.items():
        if var_name not in var_by_name:
            logger.warning(f"Variable '{var_name}' not found in SCAD file, skipping")
            continue

        var = var_by_name[var_name]
        line_idx = var.line_number - 1  # Convert to 0-indexed

        if line_idx < 0 or line_idx >= len(lines):
            logger.warning(f"Invalid line number {var.line_number} for variable '{var_name}'")
            continue

        original_line = lines[line_idx]

        # Format the new value based on type
        formatted_value = _format_value(new_value, var.type)

        # Replace value in line using regex
        # Pattern: variable_name = value;
        pattern = rf'^({re.escape(var_name)}\s*=\s*).+?(\s*;.*)$'
        replacement = rf'\g<1>{formatted_value}\g<2>'

        new_line = re.sub(pattern, replacement, original_line)
        lines[line_idx] = new_line

        logger.debug(f"Updated {var_name}: {var.value} -> {new_value}")

    return '\n'.join(lines)


def _format_value(value: Any, var_type: ScadVariableType) -> str:
    """Format a Python value for OpenSCAD syntax.

    Args:
        value: The Python value to format
        var_type: The expected SCAD variable type

    Returns:
        String representation for SCAD file
    """
    if var_type == ScadVariableType.STRING:
        # Escape quotes in string
        escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    elif var_type == ScadVariableType.BOOLEAN:
        return 'true' if value else 'false'

    elif var_type == ScadVariableType.VECTOR:
        if isinstance(value, (list, tuple)):
            elements = ', '.join(str(v) for v in value)
            return f'[{elements}]'
        return str(value)

    elif var_type == ScadVariableType.NUMBER:
        if isinstance(value, float):
            # Remove trailing zeros for cleaner output
            if value == int(value):
                return str(int(value))
            return str(value)
        return str(value)

    return str(value)


def get_scad_files(models_path: Path | None = None) -> list[str]:
    """Scan models directory for OpenSCAD files.

    Args:
        models_path: Path to scan (uses config models_path if not provided)

    Returns:
        List of relative paths to .scad files
    """
    if models_path is None:
        settings = get_settings()
        models_path = Path(settings.models_path)

    scad_files = []

    if not models_path.exists():
        logger.warning(f"Models path does not exist: {models_path}")
        return scad_files

    # Recursively find all .scad files
    for scad_file in models_path.rglob('*.scad'):
        try:
            relative_path = str(scad_file.relative_to(models_path))
            scad_files.append(relative_path)
        except ValueError:
            scad_files.append(scad_file.name)

    logger.info(f"Found {len(scad_files)} SCAD files in {models_path}")
    return sorted(scad_files)
