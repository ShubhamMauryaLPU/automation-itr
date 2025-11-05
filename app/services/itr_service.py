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

# =========================
# Configuration & Logging
# =========================

load_dotenv()

def setup_logging(verbosity: int = 1) -> None:
    level = logging.INFO if verbosity == 1 else logging.DEBUG if verbosity > 1 else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

log = logging.getLogger("itr-profile")

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
    retry_backoff_base_ms: int = 700  # with jitter
    block_media: bool = True

    login_url: str = "https://eportal.incometax.gov.in/iec/foservices/#/login"
    profile_url: str = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"


# =========================
# Utility: Retry & Waits
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


async def retry_async(
    fn: Callable[[], Awaitable[Any]],
    attempts: int,
    base_backoff_ms: int,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> Any:
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


# =========================
# Modal Handlers
# =========================

async def click_dual_login_if_present(page) -> None:
    """
    Handles 'Dual Login Detected' modal which sometimes appears after login or navigation.
    """
    try:
        # Look for dialog message or the button
        locator = page.locator("button", has_text=re.compile(r"login\s*here", re.I))
        for _ in range(6):
            if (await locator.count()) > 0:
                try:
                    await locator.first.click(timeout=2000, force=True)
                    log.info("Handled dual-login dialog (clicked 'Login Here').")
                    await page.wait_for_timeout(800)
                    return
                except Exception:
                    pass

            # Other likely selectors
            for sel in [
                'button:has-text("Login Here")',
                'button:has-text("LOGIN HERE")',
                'button.mat-button:has-text("Login Here")',
                'mat-dialog-container button:has-text("Login Here")',
            ]:
                btn = await page.query_selector(sel)
                if btn:
                    try:
                        await btn.click(timeout=2000, force=True)
                        log.info("Handled dual-login dialog (fallback selector).")
                        await page.wait_for_timeout(800)
                        return
                    except Exception:
                        pass

            await page.wait_for_timeout(300)
    except Exception:
        pass


async def click_logout_confirm_no_if_present(page) -> None:
    """
    Handles 'Are you sure you want to Logout?' confirmation modal defensively.
    """
    try:
        for _ in range(6):
            no_btn = page.locator("button", has_text=re.compile(r"^\s*No\s*$", re.I))
            yes_btn = page.locator("button", has_text=re.compile(r"^\s*Yes\s*$", re.I))

            if (await no_btn.count()) > 0 or (await yes_btn.count()) > 0:
                try:
                    if (await no_btn.count()) > 0:
                        await no_btn.first.click(timeout=1500, force=True)
                        log.info("Closed logout confirmation (clicked 'No').")
                        await page.wait_for_timeout(500)
                        return
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(350)
                        return
                    except Exception:
                        pass
            await page.wait_for_timeout(200)
    except Exception:
        pass


# =========================
# Login Steps
# =========================

async def robust_fill_user_id_and_continue(page, cfg: ScraperConfig) -> None:
    log.info("Filling User ID…")
    uid_selectors = [
        'input[formcontrolname="userId"]',
        'input[name="userId"]',
        'input[placeholder*="PAN" i]',
        'input[placeholder*="USER ID" i]',
        'input[id*="userId" i]',
        'input[aria-label*="User" i]',
    ]

    uid_handle = None
    for sel in uid_selectors:
        try:
            uid_handle = await page.wait_for_selector(sel, timeout=4000)
            if uid_handle:
                log.debug(f"User ID selector matched: {sel}")
                break
        except PlaywrightTimeoutError:
            continue

    if uid_handle:
        try:
            await uid_handle.fill("")
            await uid_handle.focus()
            await page.keyboard.type(cfg.user_id, delay=25)
            # dispatch events to satisfy Angular/React forms
            await page.evaluate(
                """(el)=>{el.dispatchEvent(new Event('input',{bubbles:true}));
                          el.dispatchEvent(new Event('change',{bubbles:true}));
                          el.dispatchEvent(new Event('blur',{bubbles:true}));}""",
                uid_handle,
            )
        except Exception as e:
            log.debug(f"Typing UID failed, injecting value: {e}")
            try:
                await page.evaluate(
                    """(el, val)=>{
                        el.value = val;
                        el.dispatchEvent(new Event('input',{bubbles:true}));
                        el.dispatchEvent(new Event('change',{bubbles:true}));
                      }""",
                    uid_handle,
                    cfg.user_id,
                )
            except Exception:
                pass
    else:
        log.warning("User ID field not detected; continuing defensively.")

    # Click Continue/Proceed
    for sel in ['button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                'button:has-text("Proceed")',
                'button:has-text("PROCEED")']:
        btn = await page.query_selector(sel)
        if btn:
            try:
                await asyncio.gather(
                    btn.click(timeout=3000),
                    page.wait_for_load_state("domcontentloaded"),
                )
                log.info("Clicked first-step Continue/Proceed.")
                return
            except Exception as e:
                log.debug(f"Continue click non-fatal: {e}")
    log.info("No explicit Continue found; proceeding.")


async def robust_fill_password_and_submit(page, cfg: ScraperConfig) -> None:
    log.info("Filling Password…")
    pwd_selectors = [
        'input[formcontrolname="password"]',
        'input[type="password"]',
        'input[id*="password" i]',
        'input[name*="password" i]',
        'input[placeholder*="password" i]',
    ]
    pwd_handle = None
    for sel in pwd_selectors:
        try:
            pwd_handle = await page.wait_for_selector(sel, timeout=6000)
            if pwd_handle:
                log.debug(f"Password selector matched: {sel}")
                break
        except PlaywrightTimeoutError:
            continue

    # Some forms require a checkbox (terms/secure access)
    try:
        chk = await page.query_selector('input[type="checkbox"]')
        if chk and not await chk.is_checked():
            await chk.click()
            log.debug("Checked auxiliary checkbox.")
    except Exception:
        pass

    if not pwd_handle:
        log.warning("Password input not found; cannot submit automatically.")
        return

    await pwd_handle.fill("")
    await pwd_handle.focus()
    await page.keyboard.type(cfg.password, delay=60)
    try:
        await page.evaluate(
            """(el)=>{el.dispatchEvent(new Event('input',{bubbles:true}));
                      el.dispatchEvent(new Event('change',{bubbles:true}));
                      el.dispatchEvent(new Event('blur',{bubbles:true}));}""",
            pwd_handle,
        )
    except Exception:
        pass

    # Submit
    for sel in ['button:has-text("Continue")',
                'button:has-text("CONTINUE")',
                'button:has-text("Proceed")',
                'button:has-text("PROCEED")',
                'button[type="submit"]']:
        btn = await page.query_selector(sel)
        if btn:
            try:
                await asyncio.gather(
                    btn.click(timeout=3000),
                    page.wait_for_load_state("domcontentloaded"),
                )
                log.info("Submitted password (clicked button).")
                await page.wait_for_timeout(900)
                return
            except Exception:
                pass

    try:
        await pwd_handle.press("Enter")
        await page.wait_for_timeout(900)
        log.info("Submitted password (pressed Enter).")
    except Exception:
        log.warning("Password submit via Enter failed.")


# =========================
# Navigation & Extraction
# =========================

async def spa_safe_goto_profile(page, cfg: ScraperConfig) -> None:
    await click_dual_login_if_present(page)
    await click_logout_confirm_no_if_present(page)

    if "profileDetail" in (page.url or ""):
        log.debug("Already on profile page.")
        return

    # Some portals use hash routing; be gentle
    log.info("Navigating to profile…")
    try:
        await page.goto(cfg.profile_url, wait_until="domcontentloaded", timeout=cfg.navigation_timeout_ms)
    except Exception as e:
        log.debug(f"Direct goto failed (non-fatal): {e}")
        # hash-change fallback
        try:
            await page.evaluate("""() => { window.location.hash = "#/dashboard/myProfile/profileDetail"; }""")
        except Exception:
            pass

    await click_dual_login_if_present(page)
    await click_logout_confirm_no_if_present(page)

    async def on_profile() -> bool:
        try:
            if "profileDetail" in (page.url or ""):
                return True
            el = await page.query_selector('app-profile-detail, div[class*="profile"]')
            return el is not None
        except Exception:
            return False

    ok = await wait_until(on_profile, timeout_ms=20000)
    if not ok:
        log.warning("Profile container not confirmed yet; continuing defensively.")


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


async def detect_2fa_blockers(page) -> Optional[str]:
    """Detect OTP / CAPTCHA / challenge screens and return a reason if found."""
    try:
        body_text = (await page.evaluate("() => document.body.innerText || ''")) or ""
        body_text = body_text.lower()
        patterns = [
            r"enter\s*otp",
            r"one[-\s]*time\s*password",
            r"captcha",
            r"verification\s*code",
            r"confirm\s*otp",
        ]
        for pat in patterns:
            if re.search(pat, body_text):
                return "OTP/CAPTCHA or verification step detected; manual completion required."
    except Exception:
        pass
    return None


async def extract_profile_data(page) -> Dict[str, Any]:
    await click_dual_login_if_present(page)
    await click_logout_confirm_no_if_present(page)

    log.info("Waiting for profile content to render…")
    await page.wait_for_timeout(3000)

    try:
        await page.wait_for_selector('app-profile-detail, div[class*="profile"], div[class*="content"]', timeout=15000)
    except PlaywrightTimeoutError:
        log.debug("Profile container selector not found within timeout.")

    profile = await page.evaluate(
        """() => {
        const norm = (t)=> typeof t === 'string' ? t.replace(/\\s+/g,' ').trim() : '';
        const safeText = (el)=>{ try { return norm(el.innerText); } catch { return '';} };
        const nodes = Array.from(document.querySelectorAll('body *')).filter(el => {
          try { return el && el.children.length === 0 && !!safeText(el); } catch { return false; }
        });

        const labels = [
          "Name of Organisation","Name of Organization","Name as per PAN",
          "Date of Incorporation","PAN","PAN Status","Residential Status",
          "Type of Company","Constitution of Business","Email","Mobile","Address"
        ];

        const fields = {};
        for (const label of labels) {
          let value = "";
          const pool = Array.from(document.querySelectorAll('div, span, p, td, th, li'));
          for (const el of pool) {
            const text = safeText(el);
            if (text === label) {
              const next = el.nextElementSibling;
              if (next && safeText(next)) { value = safeText(next); break; }
              const parentNext = el.parentElement && el.parentElement.nextElementSibling;
              if (parentNext && safeText(parentNext)) { value = safeText(parentNext); break; }
            }
          }

          if (!value) {
            for (const leaf of nodes) {
              const txt = safeText(leaf);
              if (txt.toLowerCase().includes(label.toLowerCase())) {
                const parent = leaf.closest('div, tr, li, section, article');
                if (parent) {
                  const candidates = Array.from(parent.querySelectorAll('div, span, td'))
                    .map(safeText)
                    .filter((x)=> x && x.toLowerCase() !== txt.toLowerCase());
                  if (candidates.length) { value = candidates.join(' '); break; }
                }
              }
            }
          }

          if (!value) {
            const bodyText = norm(document.body.innerText || '');
            const regex = new RegExp(label.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&') + "\\\\s*[:\\\\-]?\\\\s*([A-Za-z0-9@./,\\\\-\\\\s]+)", "i");
            const m = bodyText.match(regex);
            if (m && m[1]) value = norm(m[1]);
          }

          if (value) fields[label] = value;
        }

        return {
          nameOfOrganisation: fields["Name of Organisation"] || fields["Name of Organization"] || fields["Name as per PAN"] || "",
          dateOfIncorporation: fields["Date of Incorporation"] || "",
          pan: fields["PAN"] || "",
          panStatus: fields["PAN Status"] || "",
          residentialStatus: fields["Residential Status"] || "",
          typeOfCompany: fields["Type of Company"] || fields["Constitution of Business"] || "",
          email: fields["Email"] || "",
          mobile: fields["Mobile"] || "",
          address: fields["Address"] || "",
          raw: fields,
          title: document.title || "",
          url: location.href || "",
        };
    }"""
    )

    # Normalize whitespace
    for k in list(profile.keys()):
        if isinstance(profile[k], str):
            profile[k] = _normalize(profile[k])

    if not profile.get("pan") and not profile.get("nameOfOrganisation"):
        log.warning("No key profile fields found — possibly not on detail page yet or DOM changed.")
    else:
        log.info("Profile fields captured.")
    return profile


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
    Logs in and fetches ITR profile details. Returns dict with fields.
    May raise RuntimeError for OTP/CAPTCHA screens or irreversible failures.
    """
    setup_logging(verbosity)

    cfg = ScraperConfig(
        user_id=user_id or os.getenv("ITR_USER_ID") or "",
        password=password or os.getenv("ITR_PASSWORD") or "",
        headless=True if headless is None else bool(headless),
        user_data_dir=user_data_dir or os.getenv("USER_DATA_DIR") or None,
        chrome_path=chrome_path or os.getenv("CHROME_PATH") or None,
        save_json_path=save_json_path,
    )

    if not cfg.user_id or not cfg.password:
        raise ValueError("ITR_USER_ID and ITR_PASSWORD are required.")

    log.info(f"Starting login + profile fetch (headless={cfg.headless})")

    browser_args = [
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
    ]

    async with async_playwright() as p:
        chromium = p.chromium

        # ---- Context creation (persistent vs ephemeral) ----
        context = None
        browser = None
        try:
            launch_kwargs = {"headless": cfg.headless, "args": browser_args}
            if cfg.chrome_path:
                launch_kwargs["executable_path"] = cfg.chrome_path

            if cfg.user_data_dir:
                log.info(f"Using persistent profile: {cfg.user_data_dir}")
                context = await chromium.launch_persistent_context(cfg.user_data_dir, **launch_kwargs)
            else:
                browser = await chromium.launch(**launch_kwargs)
                context = await browser.new_context()

            if cfg.block_media:
                await context.route(
                    re.compile(r".*\.(?:png|jpg|jpeg|gif|webp|svg|mp4|webm)(?:\?.*)?$", re.I),
                    lambda route: asyncio.create_task(route.abort()),
                )

            page = await context.new_page()
            page.set_default_timeout(cfg.action_timeout_ms)

            # ---- Login flow with retries ----
            async def do_login():
                await page.goto(cfg.login_url, wait_until="domcontentloaded", timeout=cfg.navigation_timeout_ms)
                await page.wait_for_timeout(400)

                await robust_fill_user_id_and_continue(page, cfg)
                try:
                    await page.wait_for_selector(
                        'input[formcontrolname="password"], input[type="password"], input[id*="password" i]',
                        timeout=8000,
                    )
                    await robust_fill_password_and_submit(page, cfg)
                except PlaywrightTimeoutError:
                    log.debug("Password field not seen (maybe already logged in via persistent session).")

                await click_dual_login_if_present(page)
                await click_logout_confirm_no_if_present(page)
                await page.wait_for_timeout(700)

                reason = await detect_2fa_blockers(page)
                if reason:
                    raise RuntimeError(reason)

            await retry_async(
                do_login,
                attempts=cfg.retries,
                base_backoff_ms=cfg.retry_backoff_base_ms,
                on_retry=lambda n, e: log.warning(f"Login attempt {n} failed: {e}. Retrying…"),
            )

            # ---- Navigate to profile ----
            async def goto_profile():
                await spa_safe_goto_profile(page, cfg)
                # Ensure something like profile detail is present
                ok = await wait_until(
                    lambda: page.query_selector('app-profile-detail, div[class*="profile"]'),
                    timeout_ms=20000,
                )
                if not ok:
                    log.debug("Profile selector still not firm; giving SPA one more nudge.")
                    try:
                        await page.goto(cfg.profile_url, wait_until="domcontentloaded", timeout=cfg.navigation_timeout_ms)
                    except Exception:
                        pass
                await page.wait_for_timeout(cfg.idle_wait_ms)

            await retry_async(
                goto_profile,
                attempts=max(2, cfg.retries - 1),
                base_backoff_ms=cfg.retry_backoff_base_ms,
                on_retry=lambda n, e: log.warning(f"Profile nav attempt {n} failed: {e}. Retrying…"),
            )

            # ---- Extract ----
            profile = await extract_profile_data(page)

            # ---- Save JSON if requested ----
            if cfg.save_json_path:
                try:
                    with open(cfg.save_json_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "fetched_at": datetime.utcnow().isoformat() + "Z",
                                "data": profile,
                            },
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )
                    log.info(f"Saved profile JSON → {cfg.save_json_path}")
                except Exception as e:
                    log.error(f"Failed to save JSON: {e}")

            return profile

        finally:
            # Always close cleanly
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



def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Fetch Income Tax e-Filing profile details.")
    ap.add_argument("--user-id", dest="user_id", default=None, help="Portal user id / PAN (fallback: ITR_USER_ID)")
    ap.add_argument("--password", dest="password", default=None, help="Portal password (fallback: ITR_PASSWORD)")
    ap.add_argument("--headless", dest="headless", action="store_true", help="Run headless (default)")
    ap.add_argument("--headed", dest="headed", action="store_true", help="Run with UI (headed)")
    ap.add_argument("--user-data-dir", dest="user_data_dir", default=None, help="Path for persistent Chromium profile")
    ap.add_argument("--chrome-path", dest="chrome_path", default=None, help="Chromium/Chrome executable path")
    ap.add_argument("--save-json", dest="save_json_path", default=None, help="Write output JSON to this path")
    ap.add_argument("-v", "--verbose", dest="verbose", action="count", default=1, help="Increase log verbosity (-v, -vv)")
    return ap.parse_args(argv)


async def _main_async(ns: argparse.Namespace) -> int:
    headless = True
    if ns.headed:
        headless = False
    elif ns.headless:
        headless = True

    try:
        profile = await fetch_itr_profile(
            user_id=ns.user_id,
            password=ns.password,
            headless=headless,
            user_data_dir=ns.user_data_dir,
            chrome_path=ns.chrome_path,
            save_json_path=ns.save_json_path,
            verbosity=ns.verbose,
        )
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    except RuntimeError as e:
        log.error(f"Stopped due to portal challenge: {e}")
        return 2
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
        return 130
    except Exception as e:
        log.exception(f"Fatal error: {e}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_main_async(ns))


if __name__ == "__main__":
    raise SystemExit(main())
