"""
scanner.py — scans a single website end to end and returns a ScanResult.
Retries once (with a longer timeout) on a failed/timed-out attempt before
giving up, since at 10K-site scale a fraction of failures are just transient
network hiccups rather than genuinely broken sites.
"""

import logging

import files.config as config
from files.banner_detector import CollectPageSignals, WaitForLikelyBanner
from files.cmp_identifier import IdentifyCmp
from files.models import CmpEvidence, ScanResult
from files.privacy_policy import FindPrivacyPolicyLink, InspectPrivacyPolicy
from files.utils import GetWebsiteName, NormalizeUrl


def AppendToDebugLog(Evidence):
    """Human-readable per-site evidence dump, for spot-checking detections."""
    try:
        with open(config.DEBUG_LOG_PATH, "a", encoding="utf-8") as LogFile:
            LogFile.write(f"=== SCAN EVIDENCE FOR: {Evidence.Url} ===\n")
            LogFile.write(f"Title: {Evidence.Title}\n")
            LogFile.write(f"Banner Detected: {Evidence.BannerPresent}\n")
            LogFile.write(f"Matched CMP: {Evidence.CmpDetected}\n")
            LogFile.write(f"Reason: {Evidence.Reason}\n")
            LogFile.write(f"Detected IDs: {Evidence.FoundIds}\n")
            LogFile.write(f"Detected Globals: {Evidence.FoundGlobals}\n")
            LogFile.write("=" * 40 + "\n\n")
    except Exception as Ex:
        logging.debug(f"Failed writing debug log for {Evidence.Url}: {Ex}")


async def _AttemptScan(
    BrowserManagerInstance, Url, FormattedUrl, WebsiteName, GotoTimeoutMs
):
    """One scan attempt. Raises on failure so the caller can decide to retry."""
    Context = await BrowserManagerInstance.NewContext()
    try:
        Page = await Context.new_page()
        await Page.goto(
            FormattedUrl, timeout=GotoTimeoutMs, wait_until="domcontentloaded"
        )
        await WaitForLikelyBanner(Page)

        Title = await Page.title()
        Signals = await CollectPageSignals(Page)
        # CMP matching is pure Python logic over page-provided data — a page
        # that returns something unexpected can trip a bug here even after a
        # successful navigation. Isolate it so that never wastes the retry
        # budget or blocks the privacy-policy check, which is independent.
        try:
            Evidence = IdentifyCmp(Url, Title, Signals)
        except Exception as Ex:
            logging.debug(f"CMP identification failed for {Url}: {Ex}")
            Evidence = CmpEvidence(
                Url=Url,
                Title=Title or "",
                BannerPresent="Unknown",
                CmpDetected="Error",
                Reason=str(Ex),
            )
        AppendToDebugLog(Evidence)

        PolicyLink = await FindPrivacyPolicyLink(Page, FormattedUrl)
        PolicyEvidenceResult = await InspectPrivacyPolicy(
            Context, FormattedUrl, PolicyLink
        )

        Result = ScanResult(
            WebsiteUrl=Url,
            WebsiteName=WebsiteName,
            CookieBannerAvailable=Evidence.BannerPresent,
            ConsentTool=Evidence.CmpDetected,
            PrivacyPolicyUrl=PolicyEvidenceResult.PrivacyPolicyUrl,
            PrivacyPolicyHost=PolicyEvidenceResult.PrivacyPolicyHost,
            ThirdPartyHostedPolicy=PolicyEvidenceResult.ThirdPartyHosted,
            DetectedPolicyOwner=PolicyEvidenceResult.DetectedPolicyOwner,
            OwnerMatchesSite=PolicyEvidenceResult.OwnerMatchesSite,
            StatusError="Success",
        )
        return Result
    finally:
        # If the underlying browser/driver already died mid-scan, closing a
        # context that no longer exists raises its own error — don't let that
        # cleanup failure mask/override whatever actually went wrong above,
        # or get logged as if it were the real cause.
        try:
            await Context.close()
        except Exception as Ex:
            logging.debug(f"Context close failed for {Url} (already closed?): {Ex}")


async def ScanSingleWebsite(BrowserManagerInstance, Url, Semaphore):
    """
    Scans one site under the shared concurrency semaphore. Never raises —
    any unrecoverable failure comes back as a ScanResult with StatusError set,
    so one bad site can never take down a 10K-site batch run.
    """
    async with Semaphore:
        WebsiteName = GetWebsiteName(Url)
        FormattedUrl = NormalizeUrl(Url)
        logging.info(f"Starting scan: {WebsiteName}")

        LastError = None
        Attempts = [config.PAGE_GOTO_TIMEOUT_MS] + [
            config.PAGE_GOTO_RETRY_TIMEOUT_MS
        ] * config.MAX_RETRIES

        for AttemptNumber, TimeoutMs in enumerate(Attempts, start=1):
            try:
                Result = await _AttemptScan(
                    BrowserManagerInstance, Url, FormattedUrl, WebsiteName, TimeoutMs
                )
                logging.info(
                    f"Finished {WebsiteName}: Banner={Result.CookieBannerAvailable} | "
                    f"Tool={Result.ConsentTool} | Policy Owner={Result.DetectedPolicyOwner} | "
                    f"Match={Result.OwnerMatchesSite}"
                )
                return Result
            except Exception as Ex:
                LastError = Ex
                if AttemptNumber < len(Attempts):
                    logging.info(f"Retrying {WebsiteName} after error: {Ex}")

        ErrorMessage = str(LastError).splitlines()[0] if LastError else "Unknown error"
        logging.warning(
            f"Failed {WebsiteName} after {len(Attempts)} attempt(s): {ErrorMessage}"
        )
        return ScanResult(
            WebsiteUrl=Url,
            WebsiteName=WebsiteName,
            StatusError=f"Error: {ErrorMessage}",
        )
