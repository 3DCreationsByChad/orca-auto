"""Full test of slicer service with an actual network-storage file."""

import asyncio
import sys
import os

sys.path.insert(0, "/opt/orca-api/src")

from orca_api.services.slicer import slice_file, get_available_profiles, compute_output_path
from orca_api.config import get_settings

async def main():
    settings = get_settings()
    print("Testing slicer service with an actual network-storage files...")
    print(f"Models path: {settings.models_path}")
    print(f"Sliced path: {settings.sliced_path}")
    
    # Test profile listing
    profiles = get_available_profiles()
    print(f"\nAvailable profiles: {profiles}")
    
    # Find a test file in the models directory
    test_file = None
    for root, dirs, files in os.walk(settings.models_path):
        for f in files:
            if f.endswith(".stl"):
                full_path = os.path.join(root, f)
                try:
                    size = os.path.getsize(full_path)
                    if size < 500000:  # Under 500KB
                        test_file = full_path
                        break
                except:
                    continue
        if test_file:
            break
    
    if not test_file:
        print("No small STL files found for testing")
        return
    
    print(f"\nTest file: {test_file}")
    print(f"  Size: {os.path.getsize(test_file)} bytes")
    
    # Test output path computation
    output = compute_output_path(test_file, "draft")
    print(f"  Expected output: {output}")
    
    # Slice with draft profile
    print("\nSlicing with draft profile...")
    result = await slice_file(test_file, "draft")
    
    print(f"\nResult:")
    print(f"  Success: {result.success}")
    print(f"  Duration: {result.duration_seconds:.1f}s")
    
    if result.success:
        print(f"  Output: {result.output_path}")
        if os.path.exists(result.output_path):
            print(f"  Output size: {os.path.getsize(result.output_path)} bytes")
            
            # Verify output is in sliced directory
            if settings.sliced_path in result.output_path:
                print("  Output is in correct sliced directory: YES")
            else:
                print(f"  WARNING: Output not in sliced directory")
            
            # Verify filename includes profile
            if "_draft" in result.output_path:
                print("  Output filename includes profile: YES")
            else:
                print("  WARNING: Filename does not include profile")
            
            print("\nFULL SLICE TEST PASSED!")
            
            # Leave output for verification
            print(f"\nOutput left for inspection: {result.output_path}")
    else:
        print(f"  Error: {result.error_message}")
        if result.stderr:
            print(f"  Stderr: {result.stderr[:500]}")

if __name__ == "__main__":
    asyncio.run(main())
