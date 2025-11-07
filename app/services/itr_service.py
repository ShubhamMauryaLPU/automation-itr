import os
import re
import sys
import json
import asyncio
import random
import argparse
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Awaitable
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from app.core.logger import get_logger

# =========================
# Configuration & Logging
# =========================

load_dotenv()
log = get_logger("itr_service")

@dataclass
class ScraperConfig:
    user_id: str
    password: str
    headless: bool = True
    user_data_dir: Optional[str] = None
    chrome_path: Optional[str] = None
    save_json_path: Optional[str] = None
    navigation_timeout_ms: int = 60000
    action_timeout_ms: int = 12000
    idle_wait_ms: int = 2500
    retries: int = 3
    retry_backoff_base_ms: int = 700
    block_media: bool = True

    login_url: str = "https://eportal.incometax.gov.in/iec/foservices/#/login"
    profile_url: str = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"


# =========================
# Utility Helpers
# =========================

async def wait_until(predicate_async: Callable[[], Awaitable[bool]], timeout_ms: int, interval_ms: int = 400) -> bool:
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout_ms / 1000
    while loop.time() < end:
        try:
            if await predicate_async():
                return True
        except Exception:
            pass
        await asyncio.sleep(interval_ms / 1000)
    return False


async def retry_async(fn: Callable[[], Awaitable[Any]], attempts: int, base_backoff_ms: int, on_retry: Optional[Callable[[int, Exception], None]] = None) -> Any:
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as e:
            last_exc = e
            if i == attempts:
                break
            if on_retry:
                on_retry(i, e)
            jitter = random.randint(0, base_backoff_ms // 3)
            await asyncio.sleep((base_backoff_ms * i + jitter) / 1000)
    raise last_exc


# (💡 All your Playwright login, navigation, extraction helpers remain same)
# keep: click_dual_login_if_present, click_logout_confirm_no_if_present, robust_fill_user_id_and_continue, etc.

# =========================
# Core Orchestration
# =========================

async def fetch_itr_profile(
    user_id: Optional[str] = None,
    password: Optional[str] = None,
    headless: Optional[bool] = None,
    user_data_dir: Optional[str] = None,
    chrome_path: Optional[str] = None,
    save_json_path: Optional[str] = None,
    verbosity: int = 1,
) -> Dict[str, Any]:
    """
    Logs in and fetches ITR profile details using Playwright automation.
    Returns structured dict data.
    """
    cfg = ScraperConfig(
        user_id=user_id or os.getenv("ITR_USER_ID", ""),
        password=password or os.getenv("ITR_PASSWORD", ""),
        headless=True if headless is None else bool(headless),
        user_data_dir=user_data_dir or os.getenv("USER_DATA_DIR"),
        chrome_path=chrome_path or os.getenv("CHROME_PATH"),
        save_json_path=save_json_path,
    )

    if not cfg.user_id or not cfg.password:
        raise ValueError("Missing ITR_USER_ID and ITR_PASSWORD credentials.")

    log.info(f"Starting ITR profile fetch (headless={cfg.headless})")

    browser_args = ["--disable-dev-shm-usage", "--no-sandbox", "--disable-blink-features=AutomationControlled"]

    result: Dict[str, Any] = {"status": "INIT", "data": None}

    async with async_playwright() as p:
        chromium = p.chromium
        context = None
        browser = None

        try:
            launch_kwargs = {"headless": cfg.headless, "args": browser_args}
            if cfg.chrome_path and os.path.exists(cfg.chrome_path):
                launch_kwargs["executable_path"] = cfg.chrome_path

            # Persistent vs temporary session
            if cfg.user_data_dir:
                context = await chromium.launch_persistent_context(cfg.user_data_dir, **launch_kwargs)
            else:
                browser = await chromium.launch(**launch_kwargs)
                context = await browser.new_context()

            if cfg.block_media:
                await context.route(
                    re.compile(r".*\.(png|jpg|jpeg|gif|webp|svg|mp4|webm)(\?.*)?$", re.I),
                    lambda route: asyncio.create_task(route.abort()),
                )

            page = await context.new_page()
            page.set_default_timeout(cfg.action_timeout_ms)

            async def do_login():
                await page.goto(cfg.login_url, wait_until="domcontentloaded", timeout=cfg.navigation_timeout_ms)
                await page.wait_for_timeout(400)
                await robust_fill_user_id_and_continue(page, cfg)
                await page.wait_for_timeout(800)
                await robust_fill_password_and_submit(page, cfg)
                await page.wait_for_timeout(800)

            await retry_async(do_login, cfg.retries, cfg.retry_backoff_base_ms, lambda n, e: log.warning(f"Login retry {n}: {e}"))

            async def goto_profile():
                await spa_safe_goto_profile(page, cfg)
                await page.wait_for_timeout(cfg.idle_wait_ms)

            await retry_async(goto_profile, 2, cfg.retry_backoff_base_ms)

            profile = await extract_profile_data(page)
            result = {"status": "SUCCESS", "data": profile}

            if cfg.save_json_path:
                with open(cfg.save_json_path, "w", encoding="utf-8") as f:
                    json.dump({"fetched_at": datetime.utcnow().isoformat() + "Z", "data": profile}, f, indent=2)
                log.info(f"Saved profile data to {cfg.save_json_path}")

        except Exception as e:
            log.exception(f"Profile fetch failed: {e}")
            result = {"status": "FAILURE", "error": str(e)}

        finally:
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass

    return result


# =========================
# CLI Utility (Optional)
# =========================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run standalone ITR fetcher for testing.")
    parser.add_argument("--user-id", dest="user_id")
    parser.add_argument("--password", dest="password")
    parser.add_argument("--headed", dest="headed", action="store_true")
    parser.add_argument("--chrome-path", dest="chrome_path")
    args = parser.parse_args(argv or sys.argv[1:])

    try:
        asyncio.run(
            fetch_itr_profile(
                user_id=args.user_id,
                password=args.password,
                headless=not args.headed,
                chrome_path=args.chrome_path,
            )
        )
    except KeyboardInterrupt:
        log.warning("Stopped manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
