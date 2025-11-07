import os
import re
import sys
import json
import asyncio
import random
import argparse
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from app.core.logger import get_logger
from app.utils.helpers import sanitize_input

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
    loop = asyncio.get_running_loop()
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


SelectorList = List[str]


async def _wait_for_first_selector(
    page,
    selectors: SelectorList,
    *,
    description: str,
    timeout_ms: int,
) -> Any:
    """Return the first element handle matching any selector, raising a helpful error otherwise."""

    last_error: Optional[Exception] = None
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
            if element:
                return element
        except PlaywrightTimeoutError as exc:  # pragma: no cover - depends on live page
            last_error = exc
            log.debug("%s not found with selector %s", description, selector)
    selectors_joined = ", ".join(selectors)
    raise PlaywrightTimeoutError(f"Unable to locate {description}. Tried selectors: {selectors_joined}") from last_error


async def _click_first(page, selectors: SelectorList, *, description: str, timeout_ms: int) -> None:
    element = await _wait_for_first_selector(page, selectors, description=description, timeout_ms=timeout_ms)
    await element.click()


async def robust_fill_user_id_and_continue(page, cfg: ScraperConfig) -> None:
    """Fill the user id field and press continue handling minor DOM differences."""

    user_id_selectors: SelectorList = [
        'input[name="panAadhaarUserId"]',
        'input[formcontrolname="panAadhaarUserId"]',
        'input[name="userId"]',
        'input#panAdhaarUserId',
        'input[type="text"][autocomplete="username"]',
    ]
    continue_selectors: SelectorList = [
        'button[type="submit"]:has-text("Continue")',
        'button:has-text("Continue")',
        'text="Continue"',
    ]

    log.debug("Filling user id for %s", cfg.user_id)
    user_input = await _wait_for_first_selector(
        page,
        user_id_selectors,
        description="user id input",
        timeout_ms=cfg.action_timeout_ms,
    )
    await user_input.fill(cfg.user_id)
    await _click_first(page, continue_selectors, description="continue button", timeout_ms=cfg.action_timeout_ms)


async def robust_fill_password_and_submit(page, cfg: ScraperConfig) -> None:
    """Fill the password box and submit the form."""

    password_selectors: SelectorList = [
        'input[type="password"]',
        'input[formcontrolname="password"]',
        'input[name="password"]',
        'input#password',
    ]
    login_selectors: SelectorList = [
        'button[type="submit"]:has-text("Login")',
        'button:has-text("Login")',
        'text="Login"',
    ]

    password_input = await _wait_for_first_selector(
        page,
        password_selectors,
        description="password input",
        timeout_ms=cfg.action_timeout_ms,
    )
    await password_input.fill(cfg.password)
    await _click_first(page, login_selectors, description="login button", timeout_ms=cfg.action_timeout_ms)


async def spa_safe_goto_profile(page, cfg: ScraperConfig) -> None:
    """Navigate to the profile page and ensure the SPA finished routing."""

    await page.goto(cfg.profile_url, wait_until="domcontentloaded", timeout=cfg.navigation_timeout_ms)

    async def _is_on_profile() -> bool:
        base_profile_url = cfg.profile_url.split("#")[0]
        return page.url.startswith(base_profile_url)

    if not await wait_until(_is_on_profile, cfg.navigation_timeout_ms):
        raise PlaywrightTimeoutError("Timed out waiting for profile page to load")


FIELD_MAPPINGS: Dict[str, str] = {
    "Name of Organisation": "nameOfOrganisation",
    "Name of Organisation*": "nameOfOrganisation",
    "Date of Incorporation": "dateOfIncorporation",
    "PAN": "pan",
    "PAN Status": "panStatus",
    "Residential Status": "residentialStatus",
    "Type of Company": "typeOfCompany",
    "Email": "email",
    "Mobile": "mobile",
    "Address": "address",
}


async def extract_profile_data(page) -> Dict[str, Any]:
    """Extract key profile details from the profile screen."""

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(500)

    rows: List[Tuple[str, str]] = await page.evaluate(
        """
        () => {
            const pairs = [];
            const tables = Array.from(document.querySelectorAll('table'));
            for (const table of tables) {
                for (const row of Array.from(table.querySelectorAll('tr'))) {
                    const cells = row.querySelectorAll('th,td');
                    if (cells.length < 2) continue;
                    const key = cells[0].innerText?.trim();
                    const value = cells[1].innerText?.trim();
                    if (key && value) {
                        pairs.push([key, value]);
                    }
                }
            }
            if (!pairs.length) {
                const definitionTerms = Array.from(document.querySelectorAll('dt'));
                for (const term of definitionTerms) {
                    const key = term.textContent?.trim();
                    const value = term.nextElementSibling?.textContent?.trim();
                    if (key && value) {
                        pairs.push([key, value]);
                    }
                }
            }
            return pairs;
        }
        """,
    )

    raw: Dict[str, str] = {}
    for key, value in rows:
        cleaned_key = sanitize_input(key)
        cleaned_value = sanitize_input(value)
        raw[cleaned_key] = cleaned_value

    if not raw:
        raise RuntimeError("Unable to extract profile data from the page")

    structured: Dict[str, Any] = {"raw": raw}
    for original_key, mapped_key in FIELD_MAPPINGS.items():
        if original_key in raw:
            structured[mapped_key] = raw[original_key]

    structured.setdefault("title", sanitize_input(await page.title()))
    structured.setdefault("url", page.url)

    return structured

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
    try:
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

                await retry_async(
                    do_login,
                    cfg.retries,
                    cfg.retry_backoff_base_ms,
                    lambda n, e: log.warning(f"Login retry {n}: {e}"),
                )

                async def goto_profile():
                    await spa_safe_goto_profile(page, cfg)
                    await page.wait_for_timeout(cfg.idle_wait_ms)

                await retry_async(goto_profile, 2, cfg.retry_backoff_base_ms)

                profile = await extract_profile_data(page)
                result: Dict[str, Any] = {"status": "SUCCESS", "data": profile}

                if cfg.save_json_path:
                    with open(cfg.save_json_path, "w", encoding="utf-8") as f:
                        json.dump({"fetched_at": datetime.utcnow().isoformat() + "Z", "data": profile}, f, indent=2)
                    log.info(f"Saved profile data to {cfg.save_json_path}")

                return result

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

    except Exception as e:
        log.exception(f"Profile fetch failed: {e}")
        return {"status": "FAILURE", "error": str(e)}


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
