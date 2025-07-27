#!/usr/bin/env python3
"""
FPV Drone Tracking System Startup Script
This script starts the complete drone tracking system with proper error handling
"""

import sys
import subprocess
import os
import time

def check_dependencies():
    """Check if required dependencies are installed"""
    print("Checking dependencies...")
    
    required_packages = [
        'websockets',
        'aiohttp',
        'aiohttp_cors'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✓ {package} is installed")
        except ImportError:
            missing_packages.append(package)
            print(f"✗ {package} is missing")
    
    if missing_packages:
        print(f"\nMissing packages: {', '.join(missing_packages)}")
        print("Installing missing packages...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
            print("Dependencies installed successfully!")
        except subprocess.CalledProcessError:
            print("Failed to install dependencies. Please run: pip install -r requirements.txt")
            return False
    
    return True

def start_backend():
    """Start the backend server"""
    print("\n" + "="*50)
    print("Starting FPV Drone Tracking Backend...")
    print("="*50)
    
    try:
        # Start the backend
        subprocess.run([sys.executable, 'fpv_drone_backend.py'])
    except KeyboardInterrupt:
        print("\nShutting down backend...")
    except Exception as e:
        print(f"Error starting backend: {e}")

def main():
    """Main startup function"""
    print("FPV Drone Tracking System")
    print("=" * 30)
    
    # Check if we're in the right directory
    if not os.path.exists('fpv_drone_backend.py'):
        print("Error: fpv_drone_backend.py not found in current directory")
        print("Please run this script from the drone project directory")
        sys.exit(1)
    
    if not os.path.exists('fpv_drone_tracker.html'):
        print("Error: fpv_drone_tracker.html not found in current directory")
        sys.exit(1)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    print("\nSystem ready!")
    print("\nStarting servers...")
    print("- Backend API: http://localhost:8080")
    print("- WebSocket: ws://localhost:8081")
    print("- Frontend: http://localhost:8080")
    print("\nPress Ctrl+C to stop the system")
    
    # Start the backend
    start_backend()

if __name__ == '__main__':
    main()