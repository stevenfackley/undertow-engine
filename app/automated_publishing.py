"""
Automated Publishing Module
---------------------------
Uses Playwright with a persistent Chromium profile to:
1. Navigate to TikTok or Instagram.
2. Upload the generated MP4.
3. Inject a caption.
4. Submit the post.

NOTE: Headless browser automation on social platforms is inherently fragile.
These stubs document the intended flow and must be adapted to the current
UI structure of each platform.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page

# Path to the persistent Chromium profile directory.  Mount this as a Docker
# volume so login sessions survive container restarts.
CHROMIUM_PROFILE_DIR = os.environ.get(
    "CHROMIUM_PROFILE_DIR", "/data/chromium-profile"
)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/upload"
INSTAGRAM_CREATE_URL = "https://www.instagram.com/create/style/"

# ---------------------------------------------------------------------------
# Platform UI selectors – update these when the platform UI changes
# ---------------------------------------------------------------------------

# TikTok selectors
TIKTOK_FILE_INPUT = "input[type='file']"
TIKTOK_UPLOAD_PROGRESS = ".upload-progress"
TIKTOK_CAPTION_INPUT = "[data-testid='caption-input'], .caption-input"
TIKTOK_SUBMIT_BUTTON = "button[type='submit'], .btn-post"
TIKTOK_SUCCESS_INDICATOR = ".post-success, .upload-complete"

# Instagram selectors
INSTAGRAM_SELECT_BUTTON_TEXT = "Select from computer"
INSTAGRAM_UPLOAD_PROGRESS = ".mediaUpload, [aria-label='Trim']"
INSTAGRAM_NEXT_BUTTON = "button:has-text('Next'), div[role='button']:has-text('Next')"
INSTAGRAM_CAPTION_INPUT = "textarea[aria-label='Write a caption…']"
INSTAGRAM_SHARE_BUTTON = "button:has-text('Share'), div[role='button']:has-text('Share')"
INSTAGRAM_SUCCESS_INDICATOR = "span:has-text('Your reel has been shared')"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _launch_browser(playwright) -> BrowserContext:
    """Launch a persistent Chromium context with the saved user profile."""
    return playwright.chromium.launch_persistent_context(
        CHROMIUM_PROFILE_DIR,
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
    )


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------


def upload_to_tiktok(video_path: str | Path, caption: str) -> None:
    """
    Upload *video_path* to TikTok with *caption*.

    Parameters
    ----------
    video_path:
        Absolute path to the rendered MP4.
    caption:
        Post caption / description text (hashtags included).
    """
    video_path = Path(video_path)

    with sync_playwright() as pw:
        context = _launch_browser(pw)
        page: Page = context.new_page()

        page.goto(TIKTOK_UPLOAD_URL, wait_until="networkidle")

        # --- Stub: file upload (update TIKTOK_FILE_INPUT if TikTok UI changes) ---
        file_input = page.locator(TIKTOK_FILE_INPUT).first
        file_input.set_input_files(str(video_path))

        # Wait for upload progress indicator to disappear
        page.wait_for_selector(TIKTOK_UPLOAD_PROGRESS, state="hidden", timeout=120_000)

        # --- Stub: caption textarea (update TIKTOK_CAPTION_INPUT if UI changes) ---
        caption_area = page.locator(TIKTOK_CAPTION_INPUT).first
        caption_area.fill(caption)

        # --- Stub: submit button (update TIKTOK_SUBMIT_BUTTON if UI changes) ---
        page.locator(TIKTOK_SUBMIT_BUTTON).first.click()

        # Wait for confirmation
        page.wait_for_selector(TIKTOK_SUCCESS_INDICATOR, timeout=60_000)

        context.close()


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------


def upload_to_instagram(video_path: str | Path, caption: str) -> None:
    """
    Upload *video_path* to Instagram as a Reel with *caption*.

    Parameters
    ----------
    video_path:
        Absolute path to the rendered MP4.
    caption:
        Post caption / description text (hashtags included).
    """
    video_path = Path(video_path)

    with sync_playwright() as pw:
        context = _launch_browser(pw)
        page: Page = context.new_page()

        page.goto(INSTAGRAM_CREATE_URL, wait_until="networkidle")

        # --- Stub: file chooser trigger (update INSTAGRAM_SELECT_BUTTON_TEXT if UI changes) ---
        with page.expect_file_chooser() as fc_info:
            page.locator(f"button:has-text('{INSTAGRAM_SELECT_BUTTON_TEXT}')").click()
        file_chooser = fc_info.value
        file_chooser.set_files(str(video_path))

        # Wait for upload and trimming UI
        page.wait_for_selector(INSTAGRAM_UPLOAD_PROGRESS, timeout=120_000)

        # Advance through crop / filter steps
        for _ in range(3):
            next_btn = page.locator(INSTAGRAM_NEXT_BUTTON)
            next_btn.first.click()
            page.wait_for_timeout(1500)

        # --- Stub: fill caption (update INSTAGRAM_CAPTION_INPUT if UI changes) ---
        caption_area = page.locator(INSTAGRAM_CAPTION_INPUT).first
        caption_area.fill(caption)

        # --- Stub: share (update INSTAGRAM_SHARE_BUTTON if UI changes) ---
        page.locator(INSTAGRAM_SHARE_BUTTON).first.click()

        # Wait for success
        page.wait_for_selector(INSTAGRAM_SUCCESS_INDICATOR, timeout=60_000)

        context.close()
