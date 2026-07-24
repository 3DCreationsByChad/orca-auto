"""Command-line interface for orca-auto.

Usage:
    orca-auto setup                                        First-run configuration
    orca-auto slice <path> --profile <name> [--copies N]   Queue a slice job
    orca-auto list [path]                                  Browse available files
    orca-auto jobs [--limit N]                             Show recent jobs
    orca-auto profiles                                     List available profiles
    orca-auto status                                       Check API health
    orca-auto config [--url URL]                           View/set configuration
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

from . import __version__
from .client import OrcaClient
from .config import load_config, save_config, get_api_url
from .setup import run_setup
from .u1_cmd import add_u1_subparser


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


def colorize(text: str, color: str) -> str:
    """Apply color to text if terminal supports it."""
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{Colors.RESET}"


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def format_time_ago(timestamp: str) -> str:
    """Format ISO timestamp to relative time."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        else:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return timestamp[:10] if len(timestamp) > 10 else timestamp


def status_color(status: str) -> str:
    """Get color for job status."""
    if status == "completed":
        return Colors.GREEN
    elif status == "running":
        return Colors.YELLOW
    elif status == "failed":
        return Colors.RED
    elif status == "pending":
        return Colors.BLUE
    else:
        return Colors.RESET


def print_error(msg: str) -> None:
    """Print error message to stderr."""
    print(colorize(f"Error: {msg}", Colors.RED), file=sys.stderr)


def get_client() -> OrcaClient:
    """Create client with configured URL."""
    return OrcaClient(get_api_url())


# =============================================================================
# Command implementations
# =============================================================================

def cmd_slice(args) -> int:
    """Queue a slice job."""
    # Validate copies range
    if args.copies < 1 or args.copies > 99:
        print_error("Copies must be between 1 and 99")
        return 1
    
    try:
        client = get_client()
        result = client.create_slice_job(args.path, args.profile, args.copies)
        
        job_id = result.get("job_id", "unknown")
        short_id = job_id[:8]
        
        # Show copies in output if > 1
        if args.copies > 1:
            print(f"Job {colorize(short_id, Colors.CYAN)} queued for slicing {colorize(f'{args.copies}x', Colors.BOLD)} with profile {colorize(args.profile, Colors.BOLD)}")
        else:
            print(f"Job {colorize(short_id, Colors.CYAN)} queued for slicing with profile {colorize(args.profile, Colors.BOLD)}")
        return 0
    except ConnectionError as e:
        print_error(str(e))
        return 1
    except ValueError as e:
        print_error(str(e))
        return 1
    except RuntimeError as e:
        print_error(str(e))
        return 1


def cmd_list(args) -> int:
    """List files and directories."""
    try:
        client = get_client()
        files = client.list_files(args.path or "")
        
        if not files:
            print("No files found.")
            return 0
        
        # Calculate column widths
        max_name = max(len(f.get("name", "")) for f in files)
        max_name = max(max_name, 4)  # Minimum "Name" header width
        
        # Print header
        print(f"{'Name':<{max_name}}  {'Type':<9}  {'Size':>10}")
        print("-" * (max_name + 25))
        
        # Sort: directories first, then files
        # API uses 'is_dir' boolean
        dirs = [f for f in files if f.get("is_dir", False)]
        regular = [f for f in files if not f.get("is_dir", False)]
        
        for item in dirs + regular:
            name = item.get("name", "?")
            is_dir = item.get("is_dir", False)
            
            if is_dir:
                name_display = f"{name}/"
                type_str = "dir"
                size_str = "-"
                # Print with blue color for directory
                print(f"{colorize(name_display, Colors.BLUE):<{max_name + len(Colors.BLUE) + len(Colors.RESET)}}  {type_str:<9}  {size_str:>10}")
            else:
                type_str = "file"
                size_bytes = item.get("size", 0)
                size_str = format_size(size_bytes)
                print(f"{name:<{max_name}}  {type_str:<9}  {size_str:>10}")
        
        return 0
    except ConnectionError as e:
        print_error(str(e))
        return 1
    except ValueError as e:
        print_error(str(e))
        return 1


def cmd_jobs(args) -> int:
    """List recent jobs."""
    try:
        client = get_client()
        jobs = client.list_jobs(limit=args.limit)
        
        if not jobs:
            print("No jobs found.")
            return 0
        
        # Print header
        print(f"{'ID':<10}  {'Status':<12}  {'Profile':<12}  {'File':<30}  {'Time'}")
        print("-" * 80)
        
        for job in jobs:
            job_id = job.get("job_id", "?")[:8]
            status = job.get("status", "?")
            profile = job.get("profile", "?")
            file_path = job.get("file_path", "?")
            copies = job.get("copies", 1)
            
            # Truncate long file paths
            if len(file_path) > 28:
                file_path = "..." + file_path[-25:]
            
            # Add copies indicator if > 1
            if copies > 1:
                file_path = f"{file_path} ({copies}x)"
            
            # Truncate again if needed after adding copies
            if len(file_path) > 30:
                file_path = file_path[:27] + "..."
            
            # Get appropriate timestamp
            if status == "completed":
                ts = job.get("completed_at", job.get("created_at", ""))
            elif status == "running":
                ts = job.get("started_at", job.get("created_at", ""))
            else:
                ts = job.get("created_at", "")
            
            time_str = format_time_ago(ts) if ts else "?"
            
            # Colorize status - need to account for ANSI codes in padding
            colored_status = colorize(status, status_color(status))
            # Calculate visible length and add padding
            status_padding = 12 - len(status)
            status_display = colored_status + " " * status_padding
            
            print(f"{job_id:<10}  {status_display}  {profile:<12}  {file_path:<30}  {time_str}")
        
        return 0
    except ConnectionError as e:
        print_error(str(e))
        return 1
    except ValueError as e:
        print_error(str(e))
        return 1


def cmd_profiles(args) -> int:
    """List available profiles."""
    try:
        client = get_client()
        profiles = client.list_profiles()
        
        if not profiles:
            print("No profiles found.")
            return 0
        
        print(colorize("Available profiles:", Colors.BOLD))
        for profile in profiles:
            print(f"  - {profile}")
        
        return 0
    except ConnectionError as e:
        print_error(str(e))
        return 1


def cmd_status(args) -> int:
    """Check API health."""
    api_url = get_api_url()
    print(f"API URL: {colorize(api_url, Colors.CYAN)}")
    
    try:
        client = get_client()
        health = client.health()
        
        status = health.get("status", "unknown")
        version = health.get("version", "unknown")
        models_ok = health.get("models_path_accessible", False)
        profiles_ok = health.get("profiles_path_accessible", False)
        
        if status == "ok":
            print(f"Status: {colorize('healthy', Colors.GREEN)}")
        else:
            print(f"Status: {colorize(status, Colors.YELLOW)}")
        
        print(f"Version: {version}")
        print(f"Models path: {colorize('accessible', Colors.GREEN) if models_ok else colorize('not accessible', Colors.RED)}")
        print(f"Profiles path: {colorize('accessible', Colors.GREEN) if profiles_ok else colorize('not accessible', Colors.RED)}")
        
        return 0
    except ConnectionError as e:
        print(f"Status: {colorize('unreachable', Colors.RED)}")
        print_error(str(e))
        return 1


def cmd_config(args) -> int:
    """View or update configuration."""
    config = load_config()

    # Update if arguments provided
    updated = False
    if args.url:
        config["api_url"] = args.url
        updated = True
    if args.profile:
        config["default_profile"] = args.profile
        updated = True

    if updated:
        save_config(config)
        print(colorize("Configuration updated.", Colors.GREEN))

    # Display current config
    print(colorize("Current configuration:", Colors.BOLD))
    print(f"  api_url: {config.get('api_url', 'not set')}")
    if config.get("default_profile"):
        print(f"  default_profile: {config.get('default_profile')}")

    return 0


def cmd_setup(args) -> int:
    """Run interactive setup."""
    return run_setup(get_api_url())


# =============================================================================
# Main entry point
# =============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="orca-auto",
        description="OrcaSlicer CLI - Remote batch slicing for STL files",
        epilog="Examples:\n"
               "  orca-auto slice \"3d models/benchy.stl\" --profile draft\n"
               "  orca-auto slice \"3d models/part.stl\" -p standard --copies 4\n"
               "  orca-auto list \"3d models\"\n"
               "  orca-auto jobs --limit 5\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # slice command
    slice_parser = subparsers.add_parser(
        "slice",
        help="Queue a slice job",
        description="Queue an STL file for slicing with the specified profile."
    )
    slice_parser.add_argument("path", help="Path to STL file (relative to models root)")
    slice_parser.add_argument("--profile", "-p", required=True, help="Slice profile (e.g., draft, standard, quality)")
    slice_parser.add_argument("--copies", "-n", type=int, default=1, help="Number of copies to print (1-99, default: 1)")
    slice_parser.set_defaults(func=cmd_slice)
    
    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="Browse available files",
        description="List files and directories in the models folder."
    )
    list_parser.add_argument("path", nargs="?", default="", help="Subdirectory path (optional)")
    list_parser.set_defaults(func=cmd_list)
    
    # jobs command
    jobs_parser = subparsers.add_parser(
        "jobs",
        help="Show recent jobs",
        description="List recent slice jobs with their status."
    )
    jobs_parser.add_argument("--limit", "-l", type=int, default=20, help="Maximum jobs to show (default: 20)")
    jobs_parser.set_defaults(func=cmd_jobs)
    
    # profiles command
    profiles_parser = subparsers.add_parser(
        "profiles",
        help="List available profiles",
        description="Show all available slice profiles."
    )
    profiles_parser.set_defaults(func=cmd_profiles)
    
    # status command
    status_parser = subparsers.add_parser(
        "status",
        help="Check API health",
        description="Check connection to the OrcaSlicer API and show health status."
    )
    status_parser.set_defaults(func=cmd_status)
    
    # config command
    config_parser = subparsers.add_parser(
        "config",
        help="View/set configuration",
        description="View or update CLI configuration."
    )
    config_parser.add_argument("--url", help="Set API URL")
    config_parser.add_argument("--profile", help="Set default profile")
    config_parser.set_defaults(func=cmd_config)

    # setup command
    setup_parser = subparsers.add_parser(
        "setup",
        help="Interactive first-run setup",
        description="Configure Orca Auto interactively. Sets up paths and imports printer profiles."
    )
    setup_parser.set_defaults(func=cmd_setup)

    # u1 command group (local build->slice->print for the Snapmaker U1)
    add_u1_subparser(subparsers)

    return parser


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
