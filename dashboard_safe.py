# -*- coding: utf-8 -*-
"""Launcher for dashboard.py with stricter gallery path handling."""
import os

import dashboard as app


def safe_path(rel):
    base = os.path.abspath(app.GALLERY)
    full = os.path.abspath(os.path.join(base, rel or ""))
    try:
        if os.path.commonpath([base, full]) != base:
            return None
    except ValueError:
        return None
    return full


app.safe_path = safe_path


if __name__ == "__main__":
    app.main()
