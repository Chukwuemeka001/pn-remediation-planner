#!/usr/bin/env python3
"""Compatibility entry point for the local remediation planner."""

import sys

from pn_remediation.app import *  # Keep existing imports from remediation working.


if __name__ == "__main__":
    sys.exit(main())
