#!/usr/bin/python3
import subprocess
import os
from its_a_plane.display import Display


def main():
    """Main entry point for the its-a-plane application."""
    # Get directory of this script (its-a-plane.py)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Build path to web/app.py
    app_path = os.path.join(base_dir, "web", "app.py")

    # Start Flask server in background
    subprocess.Popen(["python3", app_path])

    # Start display loop
    run_text = Display()
    run_text.run()


if __name__ == "__main__":
    main()
