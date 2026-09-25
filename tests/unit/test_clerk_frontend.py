"""Tests de URLs del frontend Clerk."""

import pytest
from app.core.clerk_frontend import (
    DEFAULT_CLERK_JS_VERSION,
    clerk_browser_script_url,
    normalize_clerk_js_version,
)


def test_default_clerk_js_version_is_pinned_semver() -> None:
    assert normalize_clerk_js_version(DEFAULT_CLERK_JS_VERSION) == DEFAULT_CLERK_JS_VERSION


def test_clerk_browser_script_url_uses_pinned_version() -> None:
    url = clerk_browser_script_url("clerk.example.com", "5.125.10")
    assert url == "https://clerk.example.com/npm/@clerk/clerk-js@5.125.10/dist/clerk.browser.js"
    assert "@latest" not in url


def test_clerk_browser_script_url_strips_scheme_from_host() -> None:
    url = clerk_browser_script_url("https://clerk.example.com/", DEFAULT_CLERK_JS_VERSION)
    assert url.startswith("https://clerk.example.com/npm/")


@pytest.mark.parametrize("bad_version", ["latest", "5", "bad", ""])
def test_normalize_clerk_js_version_rejects_invalid(bad_version: str) -> None:
    with pytest.raises(ValueError):
        normalize_clerk_js_version(bad_version)
