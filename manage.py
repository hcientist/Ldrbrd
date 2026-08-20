#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ldrbrd.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available "
            "on your PYTHONPATH environment variable? Did you forget to "
            "activate a virtual environment?"
        ) from exc

    # Default `runserver` to the port from .env if no address was given.
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver" and len(sys.argv) == 2:
        port = os.environ.get("DJANGO_PORT", "8000")
        sys.argv.append(f"0.0.0.0:{port}")

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
