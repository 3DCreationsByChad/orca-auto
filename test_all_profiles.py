"""Test slicing with all profiles."""

import asyncio
import sys
import os

sys.path.insert(0, "/opt/orca-api/src")

from orca_api.services.slicer import slice_file, get_available_profiles

async def main():
    profiles = get_available_profiles()
    print(f"Testing all {len(profiles)} profiles...")
    
    test_file = "/tmp/test.stl"  # Small test file
    results = {}
    
    for profile in profiles:
        print(f"\nSlicing with {profile}...", end=" ")
        result = await slice_file(test_file, profile)
        results[profile] = result.success
        
        if result.success:
            print(f"OK ({result.duration_seconds:.1f}s)")
            # Clean up
            if os.path.exists(result.output_path):
                os.remove(result.output_path)
        else:
            print(f"FAILED: {result.error_message}")
    
    print("\n" + "="*40)
    print("Results:")
    all_passed = True
    for profile, success in results.items():
        status = "PASS" if success else "FAIL"
        print(f"  {profile}: {status}")
        if not success:
            all_passed = False
    
    print("="*40)
    if all_passed:
        print("ALL PROFILES PASSED!")
    else:
        print("SOME PROFILES FAILED")

if __name__ == "__main__":
    asyncio.run(main())
