# ELLE API Documentation - Sphinx Configuration
#
# Coexists with Jekyll in docs/ (Sphinx lives in docs/api/).
# Build: sphinx-build -W -b html docs/api docs/api/_build/html

from __future__ import annotations

import os
import sys

# Add source root to sys.path for autodoc
sys.path.insert(0, os.path.abspath("../../src"))

# -- Project information -----------------------------------------------------

project = "ELLE"
copyright = "2024, ELLE Contributors"
author = "ELLE Contributors"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Python settings
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autodoc_class_signature = "separated"

# Napoleon settings (Google/NumPy docstring support)
napoleon_google_docstrings = True
napoleon_numpy_docstrings = False
napoleon_include_init_with_doc = True

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3.10", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
}

# -- Options for HTML output -------------------------------------------------

html_theme = "furo"
html_title = "ELLE API Reference"
html_static_path = ["_static"]

# Furo theme options
html_theme_options = {
    "navigation_with_keys": True,
}

# Suppress warnings for missing modules (system-specific dependencies)
suppress_warnings = ["autodoc.import_error"]
