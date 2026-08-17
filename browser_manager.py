"""
browser_manager.py — owns the single Chromium instance the whole run shares,
and recycles it every config.BROWSER_RECYCLE_EVERY scans. Long-lived headless
Chromium processes slowly accumulate memory over thousands of navigations;
periodically closing and relaunching keeps a 10K-site run stable instead of
degrading (or OOM-crashing) a few hours in.
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright

import files.config as config


class BrowserManager:
    def __init__(self):
        self._Playwright: Optional[Playwright] = None
        self._Browser: Optional[Browser] = None
        self._ScanCount = 0
        self._Lock = asyncio.Lock()
        self._DrainTasks = set()

    async def Start(self):
        self._Playwright = await async_playwright().start()
        self._Browser = await self._LaunchBrowser()
        logging.info("Browser launched.")

    async def _LaunchBrowser(self):
        assert (
            self._Playwright is not None
        ), "Playwright must be started before launching a browser."
        return await self._Playwright.chromium.launch(
            headless=True, args=["--log-level=3", "--disable-logging"]
        )

    async def Stop(self):
        # Let any in-progress recycle drains finish (or force-close) before
        # tearing down Playwright itself, so nothing is left dangling.
        if self._DrainTasks:
            await asyncio.gather(*self._DrainTasks, return_exceptions=True)
        # Best-effort shutdown: if the driver connection already died (crashed
        # Chromium, killed process, etc.) these calls will themselves raise —
        # that's expected at this point, not a new problem, so don't let it
        # blow up with an unhandled traceback on the way out.
        try:
            if self._Browser:
                await self._Browser.close()
        except Exception as Ex:
            logging.debug(f"Browser close during shutdown failed (already dead?): {Ex}")
        try:
            if self._Playwright:
                await self._Playwright.stop()
        except Exception as Ex:
            logging.debug(
                f"Playwright stop during shutdown failed (already dead?): {Ex}"
            )
        logging.info("Browser closed.")

    async def _DrainAndClose(self, OldBrowser):
        """
        Close a retired browser only once every context opened on it has
        actually finished and closed. Closing it earlier would sever any
        scan still mid-flight on one of its contexts (Protocol error:
        "Failed to find browser context for id ...") — this is exactly the
        race a naive "close immediately" recycle would hit under concurrency.
        """
        try:
            while OldBrowser.contexts:
                await asyncio.sleep(2)
            await OldBrowser.close()
        except Exception as Ex:
            logging.debug(f"Error draining retired browser: {Ex}")

    async def _EnsureHealthy(self):
        """
        Detect a dead browser or a dead driver connection (Chromium crashed,
        got killed, or the whole Playwright Node process died — not just one
        context) and recover by relaunching from scratch. Must be called
        while holding self._Lock. Without this, every scan after a crash
        keeps hitting the same dead browser reference and fails forever.
        """
        BrowserIsDead = self._Browser is None or not self._Browser.is_connected()
        if not BrowserIsDead:
            return

        logging.warning(
            "Browser/driver connection lost — restarting Playwright and Chromium."
        )
        try:
            if self._Browser:
                await self._Browser.close()
        except Exception:
            pass
        try:
            if self._Playwright:
                await self._Playwright.stop()
        except Exception:
            pass

        self._Playwright = await async_playwright().start()
        self._Browser = await self._LaunchBrowser()
        # A crash invalidates every context handed out by the old (now closed)
        # browser anyway, so there's nothing left for pending drain tasks to
        # wait on — let them notice on their own next sleep cycle.

    async def NewContext(self):
        """
        Hand out a fresh incognito-style context. Every config.BROWSER_
        RECYCLE_EVERY scans, a new Chromium instance is launched and all
        subsequent contexts go to it immediately (no stall in concurrency);
        the retired instance keeps serving whatever contexts it already
        handed out and closes itself once they're all done. Also self-heals
        if the browser/driver connection has died outright between calls.
        """
        async with self._Lock:
            if self._Playwright is None or self._Browser is None:
                raise RuntimeError(
                    "BrowserManager.Start() must be called before NewContext()."
                )

            await self._EnsureHealthy()

            self._ScanCount += 1
            if self._ScanCount % config.BROWSER_RECYCLE_EVERY == 0:
                logging.info(
                    f"Recycling browser after {self._ScanCount} scans "
                    "(retired instance will close once its in-flight scans finish)."
                )
                OldBrowser = self._Browser
                self._Browser = await self._LaunchBrowser()
                DrainTask = asyncio.create_task(self._DrainAndClose(OldBrowser))
                self._DrainTasks.add(DrainTask)
                DrainTask.add_done_callback(self._DrainTasks.discard)
            ActiveBrowser = self._Browser
        return await ActiveBrowser.new_context()
