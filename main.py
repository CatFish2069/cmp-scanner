"""
main.py — orchestrates a full run:
1. load URLs from the input spreadsheet (robust to header quirks)
2. skip URLs already scanned successfully in a previous run (resume)
3. scan the rest concurrently (bounded by config.MAX_CONCURRENT_SITES),
   streaming each result to CSV (and Google Sheets, if configured) as it lands
4. write a final .xlsx copy for convenience

Designed to be safely interruptible and rerun on lists in the thousands:
killing the process and rerunning `python main.py` picks up where it left off.
"""

import asyncio
import logging
import os
import sys
import time

if __package__ in (None, ""):
    ProjectRoot = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if ProjectRoot not in sys.path:
        sys.path.insert(0, ProjectRoot)

import files.config as config
from files.browser_manager import BrowserManager
from files.excel_handler import (
    LoadUrlTargets,
    ResolveInputPath,
    ResultWriter,
    SetupGoogleSheetDashboard,
)
from files.scanner import ScanSingleWebsite
from files.utils import DedupeUrls, SetupLogging


class _Progress:
    """Thread-safe-enough (single-threaded asyncio) counter for periodic progress logs."""

    def __init__(self, Total):
        self.Done = 0
        self.Total = Total
        self.StartTime = time.monotonic()

    def Tick(self):
        self.Done += 1
        if self.Done % config.PROGRESS_LOG_EVERY == 0 or self.Done == self.Total:
            Elapsed = time.monotonic() - self.StartTime
            Rate = self.Done / Elapsed if Elapsed > 0 else 0
            Remaining = (self.Total - self.Done) / Rate if Rate > 0 else float("inf")
            logging.info(
                f"Progress: {self.Done}/{self.Total} "
                f"({Rate:.2f} sites/sec, ~{Remaining / 60:.1f} min remaining)"
            )


async def _ScanAndRecord(BrowserManagerInstance, Url, Semaphore, Writer, Progress):
    Result = await ScanSingleWebsite(BrowserManagerInstance, Url, Semaphore)
    Writer.WriteResult(Result)
    Progress.Tick()
    return Result


async def MainCrawler(InputPath=None, MaxConcurrentSites=config.MAX_CONCURRENT_SITES):
    try:
        InputPath = ResolveInputPath(InputPath)
    except FileNotFoundError as Ex:
        logging.error(str(Ex))
        return

    logging.info(f"Reading targets from '{InputPath}'.")
    try:
        AllUrls, ColumnInfo = LoadUrlTargets(InputPath)
    except FileNotFoundError:
        logging.error(
            f"Required data source document '{InputPath}' missing. Task aborted."
        )
        return

    if AllUrls is None:
        logging.error(
            f"Could not find a URL column in '{InputPath}'. "
            f"Expected one of {config.UrlColumnCandidates} (case/space-insensitive). "
            f"Columns found: {ColumnInfo}"
        )
        return

    if ColumnInfo != "Website URL":
        logging.info(f"Using column '{ColumnInfo}' as the URL source.")
    logging.info(
        f"Successfully loaded {len(AllUrls)} operational targets out of source sheet."
    )

    AllUrls, DuplicateCount = DedupeUrls(AllUrls)
    if DuplicateCount:
        logging.info(
            f"Removed {DuplicateCount} duplicate URL(s) (same domain, different formatting) — "
            f"{len(AllUrls)} unique sites to scan."
        )

    Dashboard = SetupGoogleSheetDashboard()
    Writer = ResultWriter(Dashboard=Dashboard)

    UrlTargets = [Url for Url in AllUrls if Url not in Writer.AlreadyDone]
    SkippedCount = len(AllUrls) - len(UrlTargets)
    if SkippedCount:
        logging.info(
            f"Skipping {SkippedCount} URL(s) already completed in a previous run."
        )
    if not UrlTargets:
        logging.info("Nothing left to scan — all targets already completed.")
        Writer.Close()
        Writer.ExportXlsx()
        return

    BrowserManagerInstance = BrowserManager()
    await BrowserManagerInstance.Start()
    Semaphore = asyncio.Semaphore(MaxConcurrentSites)
    Progress = _Progress(len(UrlTargets))

    try:
        Tasks = [
            _ScanAndRecord(BrowserManagerInstance, Url, Semaphore, Writer, Progress)
            for Url in UrlTargets
        ]
        await asyncio.gather(*Tasks)
    finally:
        await BrowserManagerInstance.Stop()
        Writer.Close()
        Writer.ExportXlsx()

    logging.info(
        f"Process finalized. Local analytical audit stored at: {config.OUTPUT_CSV_PATH}"
    )


_BenignShutdownPhrases = (
    "Target page, context or browser has been closed",
    "Connection closed while reading from the driver",
)


def _AsyncioExceptionHandler(loop, context):
    """
    Playwright's internal driver connection creates its own background
    Futures for protocol bookkeeping; if the driver process dies (Chromium
    crashed/killed), some of those reject with a TargetClosedError that
    nothing in our code was ever going to await directly, which asyncio
    otherwise logs as an alarming "Future exception was never retrieved".
    Downgrade only that known-benign case; anything else still surfaces
    through asyncio's normal handler.
    """
    Message = str(context.get("exception") or context.get("message") or "")
    if any(Phrase in Message for Phrase in _BenignShutdownPhrases):
        logging.debug(f"Suppressed benign shutdown noise: {Message}")
        return
    loop.default_exception_handler(context)


if __name__ == "__main__":
    import sys

    SetupLogging()
    # Windows' proactor event loop logs benign "socket.send() raised exception"
    # warnings while tearing down Chromium's subprocess pipes on Ctrl+C — not
    # an actual problem, just noise. Quiet asyncio's own logger so it doesn't
    # clutter the log during an interrupt.
    logging.getLogger("asyncio").setLevel(logging.ERROR)

    ExplicitPath = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        with asyncio.Runner() as Runner:
            Runner.get_loop().set_exception_handler(_AsyncioExceptionHandler)
            Runner.run(MainCrawler(InputPath=ExplicitPath))
    except KeyboardInterrupt:
        logging.info(
            "Scan interrupted by user. Progress up to this point is saved in "
            f"{config.OUTPUT_CSV_PATH} — rerun `python main.py` to resume."
        )
        sys.exit(0)
