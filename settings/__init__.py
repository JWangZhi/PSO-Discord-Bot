"""Centralized settings package.

Use these modules as canonical config entrypoints:
- settings.env: secrets and environment-backed runtime toggles
- settings.app: application-level operational defaults
- settings.scraper: wiki scraper specific settings
"""

from dotenv import load_dotenv

load_dotenv()  # Must run before any submodule reads os.getenv()
