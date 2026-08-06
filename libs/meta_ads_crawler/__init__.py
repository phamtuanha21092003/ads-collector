"""Backward-compatible import for MetaAdsCrawler.

The implementation lives with the Selenium response tooling.
"""

from .selenium_response_filter import MetaAdsCrawler

__all__ = ["MetaAdsCrawler"]
