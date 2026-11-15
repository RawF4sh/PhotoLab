"""A developer's own profile folder must not change test results (the own runner does the same)."""

import os

os.environ["AUTOEDIT_PROFILE"] = ""
