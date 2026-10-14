#!/usr/bin/env python3
"""Compatibility entry for the old ~/bin macro path; use the bundled singleton."""
import os

os.execv("/usr/bin/python3", ["/usr/bin/python3", "/opt/bitedj-launcher/bitedj-hold.py"])
