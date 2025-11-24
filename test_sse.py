#!/usr/bin/env python3
"""
Test script to verify SSE version endpoint is working correctly.
Usage: python test_sse.py [base_url]
Example: python test_sse.py http://localhost:8000
"""

import sys
import time
import requests


def test_sse_endpoint(base_url="http://localhost:8000"):
    """Test the SSE /events/version endpoint"""
    url = f"{base_url}/events/version"
    print(f"Connecting to SSE endpoint: {url}")
    print("Press Ctrl+C to stop\n")
    
    try:
        with requests.get(url, stream=True, timeout=60) as response:
            print(f"Status Code: {response.status_code}")
            print(f"Headers:")
            for header, value in response.headers.items():
                print(f"  {header}: {value}")
            print()
            
            if response.status_code != 200:
                print(f"ERROR: Expected status 200, got {response.status_code}")
                return False
            
            # Check required headers
            required_headers = {
                "content-type": "text/event-stream",
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
                "connection": "keep-alive",
            }
            
            missing_headers = []
            for header, expected_value in required_headers.items():
                actual_value = response.headers.get(header, "").lower()
                if expected_value.lower() not in actual_value:
                    missing_headers.append(f"{header}: expected '{expected_value}', got '{actual_value}'")
            
            if missing_headers:
                print("WARNING: Missing or incorrect headers:")
                for msg in missing_headers:
                    print(f"  - {msg}")
                print()
            else:
                print("✓ All required SSE headers are present\n")
            
            # Read and display SSE events
            print("Waiting for SSE events...")
            print("-" * 60)
            
            event_count = 0
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    print(f"[{time.strftime('%H:%M:%S')}] {line}")
                    if line.startswith("event:") or line.startswith("data:"):
                        event_count += 1
                        if event_count % 4 == 0:  # Every 2 events (event + data lines)
                            print()
    
    except KeyboardInterrupt:
        print("\n\nTest stopped by user")
        return True
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Cannot connect to {url}")
        print(f"Details: {e}")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    success = test_sse_endpoint(base_url)
    sys.exit(0 if success else 1)

