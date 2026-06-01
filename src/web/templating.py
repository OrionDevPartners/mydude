"""Shared Jinja2Templates instance with product branding registered as a global.

All route modules import `templates` from here so every template can reference
the `product` global (name, short_name, tagline) sourced from `branding.py`.
"""

from fastapi.templating import Jinja2Templates

from src.web.branding import PRODUCT

templates = Jinja2Templates(directory="templates")
templates.env.globals["product"] = PRODUCT
