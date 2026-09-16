#!/usr/bin/env python3
"""MicroSD uploader command entry point; help needs only the standard library."""
from sd_uploader.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
