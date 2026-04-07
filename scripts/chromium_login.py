"""
First-time Chromium login helper.

Run this once on the server to authenticate TikTok and Instagram inside the
persistent Chromium profile. Subsequent worker runs will reuse the saved
session cookies.

Usage (from repo root):
    docker compose run --rm -it \
        -e CHROMIUM_PROFILE_DIR=/data/chromium-profile \
        --entrypoint python worker scripts/chromium_login.py
"""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.environ.get("CHROMIUM_PROFILE_DIR", "/data/chromium-profile")

SITES = {
    "TikTok": "https://www.tiktok.com/login",
    "Instagram": "https://www.instagram.com/accounts/login/",
}


def main() -> None:
    print(f"Using Chromium profile: {PROFILE_DIR}")
    print("A browser window will open. Log in to each platform, then return here.\n")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

        for name, url in SITES.items():
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            input(f"  Log in to {name} in the browser, then press Enter here...")
            page.close()

        context.close()

    print("\nProfile saved. You can now start the worker — sessions will be reused.")


if __name__ == "__main__":
    main()
