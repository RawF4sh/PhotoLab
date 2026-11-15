"""A developer's own profile folder must not change test results (the own runner does the same)."""

import os

os.environ["AUTOEDIT_PROFILE"] = ""
os.environ["AUTOEDIT_NO_LOCAL_MODEL"] = "1"   # no test may reach a real model on the developer's machine
