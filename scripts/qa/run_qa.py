"""Local QA server on 127.0.0.1:8001 using the seeded QA copy (instance/teamos-qa.db). Never the live database."""
import os
import runpy
from pathlib import Path

root = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "instance" / "teamos-qa.db").replace("\\", "/")
os.environ.setdefault("PORT", "8001")
os.environ["MFA_REQUIRED_ROLES"] = ""
runpy.run_path(str(root / "run_local.py"), run_name="__main__")
