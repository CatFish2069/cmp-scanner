import asyncio
import logging
import os
import pandas as pd
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# --- Google Sheets ---
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("scan.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

DebugLogFile = "cmp_debug.log"

# --- CMP signature registry ---
# Each entry lists the DOM ids, CSS classes, script sources, and window globals
# known to be used by that CMP/site. AnalyzePageEvidence checks a page against
# these in order and stops at the first match.
CmpRegistry = {
    "OneTrust": {
        "Ids": ["onetrust-consent-sdk", "onetrust-banner-sdk"],
        "Classes": ["ot-sdk-container"],
        "Scripts": ["onetrust.com", "otBannerSdk.js"],
        "Globals": ["OneTrust", "OnetrustActiveGroups"],
    },
    "Cookiebot": {
        "Ids": ["CookiebotWidget", "CybotCookiebotDialog"],
        "Classes": ["CookieConsent"],
        "Scripts": ["cookiebot.com/uc.js"],
        "Globals": ["Cookiebot"],
    },
    "TrustArc": {
        "Ids": ["truste-consent-track", "trustarcNoticeFrame"],
        "Classes": ["trustarc-banner"],
        "Scripts": ["trustarc.com"],
        "Globals": ["truste"],
    },
    "Didomi": {
        "Ids": ["didomi-host"],
        "Classes": ["didomi-consent-popup"],
        "Scripts": ["didomi.io"],
        "Globals": ["didomiOnReady", "didomiState"],
    },
    "Usercentrics": {
        "Ids": ["usercentrics-root"],
        "Classes": [],
        "Scripts": ["usercentrics.js"],
        "Globals": ["UC_UI"],
    },
    "Microsoft Custom": {
        "Ids": ["uhfCookieAlert", "mscom-cookie-banner"],
        "Classes": ["ms-cookie-banner"],
        "Scripts": [],
        "Globals": ["mscc", "WcpConsent"],
    },
    "Amazon Custom": {
        "Ids": ["sp-cc", "nav-cookie-banner", "a-page-modal"],
        "Classes": [],
        "Scripts": [],
        "Globals": [],
    },
    "GitHub Custom": {
        "Ids": ["ms-consent-banner-main-styles"],
        "Classes": [],
        "Scripts": [],
        "Globals": [],
    },
    "OpenAI Custom": {
        "Ids": ["cookieConsentTitle"],
        "Classes": [],
        "Scripts": [],
        "Globals": [],
    },
    "Walmart Custom": {
        "Ids": ["third-party-cookie-detection", "tb-cookie-banner"],
        "Classes": ["cookie-banner-container"],
        "Scripts": [],
        "Globals": [],
    },
    "Lego Custom": {
        "Ids": [],
        "Classes": ["AgeGate_age-gate__cookie-disclaimer__4P12C"],
        "Scripts": [],
        "Globals": [],
    },
    "Samsung Custom": {
        "Ids": [],
        "Classes": ["cookie-bar cookie-bar--type-manage"],
        "Scripts": [],
        "Globals": [],
    },
    "Target Custom": {
        "Ids": [],
        "Classes": ["styles__CookieNoticeContainer-sc", "CookieBanner"],
        "Scripts": [],
        "Globals": [],
    },
    "IKEA Custom": {
        "Ids": ["onetrust-consent-sdk"],
        "Classes": ["hnf-cookie-banner"],
        "Scripts": [],
        "Globals": [],
    },
    "Google Custom": {
        "Ids": ["consent-bump"],
        "Classes": ["dbsfN"],
        "Scripts": ["consent.google.com"],
        "Globals": [],
    },
    "Apple Custom": {
        "Ids": ["ac-cookie-prompt"],
        "Classes": ["ac-cookie-prompt-banner"],
        "Scripts": [],
        "Globals": [],
    },
    "Meta/Facebook Custom": {
        "Ids": ["cookie-banner"],
        "Classes": ["_9bq4", "fb_cookie_banner"],
        "Scripts": [],
        "Globals": [],
    },
    "Netflix Custom": {
        "Ids": ["cookie-disclosure"],
        "Classes": ["cookie-disclosure-container"],
        "Scripts": [],
        "Globals": ["netflix"],
    },
}


def GetWebsiteName(Url):
    """Return a clean domain name for a URL, e.g. 'amazon.com' instead of the full URL."""
    try:
        ParsedUrl = urlparse(Url)
        DomainName = ParsedUrl.netloc if ParsedUrl.netloc else ParsedUrl.path
        return DomainName.replace("www.", "")
    except Exception as Ex:
        logging.error(f"Failed parsing domain name: {Ex}")
        return "Unknown"


def AppendToDebugLog(Entry):
    """Append full evidence for one scan (matched ids/globals/reason) to cmp_debug.log."""
    try:
        with open(DebugLogFile, "a", encoding="utf-8") as FileStream:
            FileStream.write(f"=== SCAN EVIDENCE FOR: {Entry['Url']} ===\n")
            FileStream.write(f"Title: {Entry['Title']}\n")
            FileStream.write(f"Banner Detected: {Entry['BannerPresent']}\n")
            FileStream.write(f"Matched CMP: {Entry['CmpDetected']}\n")
            FileStream.write(f"Reason: {Entry['Reason']}\n")
            FileStream.write(f"Detected IDs: {Entry['FoundIds']}\n")
            FileStream.write(f"Detected Globals: {Entry['FoundGlobals']}\n")
            FileStream.write("=" * 40 + "\n\n")
    except Exception as Ex:
        logging.error(f"Failed writing to debug log: {Ex}")


async def AnalyzePageEvidence(Page, Url):
    """
    Inspect a loaded page's DOM ids, classes, script sources, and window globals,
    and match them against CmpRegistry to identify which CMP (if any) is present.
    Falls back to a generic keyword scan if no registry entry matches.
    """
    Evidence = {
        "Url": Url,
        "Title": "",
        "BannerPresent": "No",
        "CmpDetected": "N/A",
        "Reason": "None",
        "FoundIds": [],
        "FoundGlobals": [],
    }

    try:
        Evidence["Title"] = await Page.title()

        PageIds = await Page.evaluate(
            "() => Array.from(document.querySelectorAll('[id]')).map(el => el.id)"
        )
        ScriptSrcs = await Page.evaluate(
            "() => Array.from(document.querySelectorAll('script[src]')).map(el => el.src)"
        )
        PageClasses = await Page.evaluate(
            "() => Array.from(document.querySelectorAll('[class]')).map(el => el.className)"
        )

        # Generic fallback: flag anything with obviously consent-related id/class
        # names, even if it doesn't match a known CMP signature.
        GenericKeywords = ["cookie", "consent", "privacy-banner", "notice-banner"]
        GenericMatchData = await Page.evaluate(f"""() => {{
            let Keywords = {GenericKeywords};
            for (let El of document.querySelectorAll('*')) {{
                if (El.id && Keywords.some(K => El.id.toLowerCase().includes(K))) {{
                    return 'ID: ' + El.id;
                }}
                if (El.className && typeof El.className === 'string' && Keywords.some(K => El.className.toLowerCase().includes(K))) {{
                    return 'Class: ' + El.className;
                }}
            }}
            return null;
        }}""")

        if GenericMatchData:
            Evidence["BannerPresent"] = "Yes"
            Evidence["CmpDetected"] = "Unknown"
            Evidence["Reason"] = f"Generic DOM match ({GenericMatchData})"

        # Check against known CMP signatures; stop at first confirmed match.
        for CmpName, Sigs in CmpRegistry.items():

            MatchedIds = [Id for Id in Sigs["Ids"] if Id in PageIds]
            if MatchedIds:
                Evidence["BannerPresent"] = "Yes"
                Evidence["CmpDetected"] = CmpName
                Evidence["Reason"] = f"ID Match ({MatchedIds[0]})"
                Evidence["FoundIds"].extend(MatchedIds)
                break

            MatchedClasses = [
                Class
                for Class in Sigs["Classes"]
                if any(Class in PClass for PClass in PageClasses)
            ]
            if MatchedClasses:
                Evidence["BannerPresent"] = "Yes"
                Evidence["CmpDetected"] = CmpName
                Evidence["Reason"] = f"Class Match ({MatchedClasses[0]})"
                break

            MatchedScripts = [
                Script
                for Script in Sigs["Scripts"]
                if any(Script in Src for Src in ScriptSrcs)
            ]
            if MatchedScripts:
                Evidence["BannerPresent"] = "Yes"
                Evidence["CmpDetected"] = CmpName
                Evidence["Reason"] = f"Script Match ({MatchedScripts[0]})"
                break

            for GlobalVar in Sigs["Globals"]:
                HasGlobal = await Page.evaluate(
                    f"() => typeof window.{GlobalVar} !== 'undefined'"
                )
                if HasGlobal:
                    Evidence["BannerPresent"] = "Yes"
                    Evidence["CmpDetected"] = CmpName
                    Evidence["Reason"] = f"Global Window Variable Match ({GlobalVar})"
                    Evidence["FoundGlobals"].append(GlobalVar)
                    break

    except Exception as Ex:
        logging.debug(f"Error extracting evidence data points for {Url}: {Ex}")

    return Evidence


async def ScanSingleWebsite(Browser, Url, Semaphore, Dashboard):
    """Open one URL in an isolated browser context, run detection, and record the result."""
    async with Semaphore:
        WebsiteName = GetWebsiteName(Url)
        logging.info(f"Starting scan: {Url}")

        FormattedUrl = Url if Url.startswith("http") else "https://" + Url

        # Use a normal desktop user agent to reduce the chance of bot blocking.
        Context = await Browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        Page = await Context.new_page()

        Result = {
            "Website URL": Url,
            "Website Name": WebsiteName,
            "Cookie Banner Available": "Unknown",
            "Consent Tool": "Unknown",
            "Status/Error": "Error",
        }

        try:
            # domcontentloaded (rather than full load) avoids stalling on heavy
            # ad/tracking assets that never finish loading.
            await Page.goto(FormattedUrl, timeout=40000, wait_until="domcontentloaded")
            # Give slow, JS-injected consent banners time to render.
            await Page.wait_for_timeout(10000)

            Evidence = await AnalyzePageEvidence(Page, Url)

            Result["Cookie Banner Available"] = Evidence["BannerPresent"]
            Result["Consent Tool"] = Evidence["CmpDetected"]
            Result["Status/Error"] = "Success"

            AppendToDebugLog(Evidence)
            logging.info(
                f"Finished {WebsiteName}: Banner={Result['Cookie Banner Available']} | Tool={Result['Consent Tool']}"
            )

        except asyncio.TimeoutError:
            Result["Status/Error"] = "Timeout"
            logging.warning(f"Timeout occurred loading page: {WebsiteName}")
        except Exception as Ex:
            Result["Status/Error"] = "Failed to load URL"
            logging.error(
                f"Fatal exception during handling of {WebsiteName}: {str(Ex)[:60]}"
            )
        finally:
            await Context.close()

        # Push the result to the live Google Sheet, if one is configured.
        if Dashboard:
            try:
                # Run the blocking gspread call in a worker thread so it doesn't
                # block the event loop.
                await asyncio.to_thread(
                    Dashboard.append_row,
                    [
                        Result["Website URL"],
                        Result["Website Name"],
                        Result["Cookie Banner Available"],
                        Result["Consent Tool"],
                        Result["Status/Error"],
                    ],
                )
            except Exception as Ex:
                logging.error(
                    f"Failed real-time upload to cloud sheet for domain {WebsiteName}: {Ex}"
                )

        return Result


async def MainCrawler(InputPath, OutputPath, MaxConcurrentSites=5):
    """Read the input spreadsheet, scan every URL concurrently, and write the results out."""

    Dashboard = None
    Scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        logging.info("Authorizing connection credentials with Google Workspace API...")
        CredentialsInstance = Credentials.from_service_account_file(
            "credentials.json", scopes=Scopes
        )
        GspreadClient = gspread.authorize(CredentialsInstance)
        Dashboard = GspreadClient.open("Crawler Dashboard").sheet1

        # Add headers if the sheet is empty.
        if len(Dashboard.get_all_values()) == 0:
            Dashboard.append_row(
                [
                    "Website URL",
                    "Website Name",
                    "Cookie Banner Available",
                    "Consent Tool",
                    "Status/Error",
                ]
            )
        logging.info(
            "Cloud verification completed. Tracking engine now broadcasting live."
        )
    except Exception as Ex:
        logging.warning(f"Cloud initialization bypassed: {Ex}")
        logging.warning(
            "Continuing execution inside offline backup storage mode. Verify authorization files."
        )

    if not os.path.exists(InputPath):
        logging.error(
            f"Required data source document '{InputPath}' missing. Task aborted."
        )
        return

    InputDataFrame = pd.read_excel(InputPath)
    UrlTargets = InputDataFrame["Website URL"].dropna().tolist()
    logging.info(
        f"Successfully loaded {len(UrlTargets)} operational targets out of source sheet."
    )

    async with async_playwright() as PlaywrightInstance:
        BrowserInstance = await PlaywrightInstance.chromium.launch(headless=True)
        SemaphoreInstance = asyncio.Semaphore(MaxConcurrentSites)

        TaskQueue = [
            ScanSingleWebsite(BrowserInstance, Url, SemaphoreInstance, Dashboard)
            for Url in UrlTargets
        ]

        OutputCollection = await asyncio.gather(*TaskQueue)
        await BrowserInstance.close()

    OutputDataFrame = pd.DataFrame(OutputCollection)
    OutputDataFrame = OutputDataFrame[
        [
            "Website URL",
            "Website Name",
            "Cookie Banner Available",
            "Consent Tool",
            "Status/Error",
        ]
    ]
    OutputDataFrame.to_excel(OutputPath, index=False)
    logging.info(f"Process finalized. Local analytical audit stored at: {OutputPath}")


if __name__ == "__main__":
    InputFileSource = "input_urls.xlsx"
    OutputFileDestination = "output_results.xlsx"

    asyncio.run(
        MainCrawler(InputFileSource, OutputFileDestination, MaxConcurrentSites=5)
    )
