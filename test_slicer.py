"""Test script for slicer service."""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, "/opt/orca-api/src")

from orca_api.services.slicer import slice_file, get_available_profiles

async def main():
    print("Testing slicer service...")
    
    # Test profile listing
    profiles = get_available_profiles()
    print(f"Available profiles: {profiles}")
    assert len(profiles) == 4, f"Expected 4 profiles, got {len(profiles)}"
    
    # Use local copy of test file to avoid Nextcloud I/O issues
    test_file = "/tmp/test.stl"
    if not os.path.exists(test_file):
        print(f"ERROR: Test file not found: {test_file}")
        print("Please copy a small STL to /tmp/test.stl first")
        return
    
    print(f"\nSlicing {test_file} with draft profile...")
    result = await slice_file(test_file, "draft")
    
    print(f"\nResult:")
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output_path}")
    print(f"  Duration: {result.duration_seconds:.1f}s")
    
    if not result.success:
        print(f"  Error: {result.error_message}")
        if result.stderr:
            print(f"  Stderr: {result.stderr[:500]}")
    else:
        # Verify output file exists
        if os.path.exists(result.output_path):
            size = os.path.getsize(result.output_path)
            print(f"  Output size: {size} bytes")
            print("\nSLICE TEST PASSED!")
            
            # Clean up test output
            os.remove(result.output_path)
            print(f"Cleaned up: {result.output_path}")
        else:
            print("ERROR: Output file not found")

if __name__ == "__main__":
    asyncio.run(main())
