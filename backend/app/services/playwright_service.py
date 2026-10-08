"""Owns the Playwright browser. One persistent context (the WordPress session) + throw-away browsers for source sites."""
from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app.config import cfg
from app.utils.errors import AppError


class PlaywrightService:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self.headless: bool = cfg.headless

    @property
    def running(self) -> bool:
        return self._ctx is not None

    @property
    def context(self) -> BrowserContext:
        if self._ctx is None:
            raise AppError("browser_not_started", "The browser is not running. Connect to WordPress first.", 409)
        return self._ctx

    async def start(self, headless: bool | None = None) -> BrowserContext:
        async with self._lock:
            headless = self.headless if headless is None else headless
            if self._ctx is not None and headless == self.headless:
                return self._ctx
            await self._stop_unlocked()
            self.headless = headless
            cfg.profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._pw = await async_playwright().start()
                self._ctx = await self._pw.chromium.launch_persistent_context(
                    str(cfg.profile_dir), headless=headless, viewport={"width": 1366, "height": 900}, locale="en-US",
                )
            except Exception as e:  # noqa: BLE001
                await self._stop_unlocked()
                raise AppError(
                    "browser_start_failed",
                    f"Could not start Chromium ({type(e).__name__}). Run: playwright install chromium",
                    500,
                )
            self._ctx.set_default_timeout(30_000)
            return self._ctx

    async def _stop_unlocked(self) -> None:
        for closer in (self._ctx, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._ctx = self._browser = None

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_unlocked()

    async def clear_session(self) -> None:
        """Logout: close the browser and delete the stored profile (cookies, storage)."""
        await self.stop()
        shutil.rmtree(cfg.profile_dir, ignore_errors=True)
        cfg.profile_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def ephemeral_context(self, *, headless: bool = True, disable_http2: bool = False):
        """A clean, cookie-less browser for untrusted source websites (never shares the WordPress session).

        Many shops reject headless Chromium: we hide the automation flag, send a normal Chrome user agent that matches the
        real browser version, and offer HTTP/1.1 (disable_http2) and a visible window (headless=False) as retry options."""
        if self._pw is None:
            await self.start()
        assert self._pw is not None
        args = ["--disable-blink-features=AutomationControlled"]
        if disable_http2:
            args.append("--disable-http2")
        browser = await self._pw.chromium.launch(headless=headless, args=args)
        try:
            ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  f"Chrome/{browser.version} Safari/537.36")
            ctx = await browser.new_context(user_agent=ua, viewport={"width": 1366, "height": 900}, locale="en-US",
                                            ignore_https_errors=True)
            await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            ctx.set_default_timeout(45_000)
            try:
                yield ctx
            finally:
                await ctx.close()
        finally:
            await browser.close()