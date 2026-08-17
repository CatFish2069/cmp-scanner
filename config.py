"""
config.py — all tunables and static registries in one place.

Nothing in here talks to the network or the filesystem beyond reading these
constants; scanner.py / browser_manager.py / excel_handler.py import from
here rather than hardcoding values, so a 10K-site run can be retuned (bigger
input files, different concurrency, a new CMP signature) without touching
logic code.
"""

import re

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
INPUT_PATH = "input_urls.xlsx"  # default, kept for backward compatibility
INPUT_PATH_CANDIDATES = ["input_urls.csv", "input_urls.xlsx", "input_urls.xls"]
OUTPUT_CSV_PATH = "output_results.csv"  # primary output — streamed row by row
OUTPUT_XLSX_PATH = (
    "output_results.xlsx"  # convenience copy, built from the CSV at the end
)
SCAN_LOG_PATH = "scan.log"
DEBUG_LOG_PATH = "cmp_debug.log"
CREDENTIALS_PATH = "credentials.json"
GOOGLE_SHEET_NAME = "Crawler Dashboard"

# ---------------------------------------------------------------------------
# Concurrency / timing — tuned for runs in the thousands of sites
# ---------------------------------------------------------------------------
MAX_CONCURRENT_SITES = 15  # browser tabs in flight at once
BROWSER_RECYCLE_EVERY = 300  # relaunch Chromium after this many scans (memory hygiene)
PAGE_GOTO_TIMEOUT_MS = 60000
PAGE_GOTO_RETRY_TIMEOUT_MS = 60000  # longer timeout used on the single retry attempt
BANNER_WAIT_TIMEOUT_MS = 20000  # early-exit wait for a banner selector to appear
POLICY_GOTO_TIMEOUT_MS = 20000
MAX_RETRIES = 1  # extra attempts for Timeout/Failed results
WRITER_FLUSH_EVERY = 20  # fsync the CSV every N rows so a crash loses little
PROGRESS_LOG_EVERY = 1  # log a "N / total done" line this often

# ---------------------------------------------------------------------------
# Output schema — single source of truth for CSV header / sheet header /
# dict shape returned by scanner.ScanSingleWebsite
# ---------------------------------------------------------------------------
OUTPUT_COLUMNS = [
    "Website URL",
    "Website Name",
    "Cookie Banner Available",
    "Consent Tool",
    "Privacy Policy URL",
    "Privacy Policy Host",
    "Third-Party Hosted Policy",
    "Detected Policy Owner",
    "Owner Matches Site",
    "Status/Error",
]

# ---------------------------------------------------------------------------
# CMP signature registry
# ---------------------------------------------------------------------------
# Each entry lists the DOM ids, CSS classes, script sources, and window
# globals known to be used by that CMP. cmp_identifier.py checks a page
# against these, in dict order, and stops at the first confirmed match.
# Signatures are sourced from vendor docs / real implementation snippets,
# not guessed — an unmatched page must resolve to "Unknown", never a guess.
CmpRegistry = {
    "OneTrust": {
        "Ids": [
            "onetrust-consent-sdk",
            "onetrust-banner-sdk",
            "onetrust-pc-sdk",
            "onetrust-pc-btn-handler",
            "onetrust-accept-btn-handler",
            "onetrust-reject-all-handler",
            "onetrust-close-btn-container",
            "onetrust-policy-text",
            "onetrust-policy-title",
            "onetrust-group-container",
            "onetrust-consent-sdk-preferences",
            "ot-sdk-btn-floating",
            "ot-pc-content",
            "onetrust-header-container",
            "onetrust-button-group",
        ],
        "Classes": [
            "ot-sdk-container",
            "ot-sdk-row",
            "otFlat",
            "ot-floating-button",
            "onetrust-pc-dark-filter",
            "ot-pc-header",
            "ot-cat-item",
            "otPcCenter",
            "ot-btn-container",
        ],
        "Scripts": [
            "cdn.cookielaw.org",
            "cmp-cdn.cookielaw.org",
            "otSDKStub.js",
            "otBannerSdk.js",
            "onetrust.com",
            "geolocation.onetrust.com",
        ],
        "Globals": [
            "OneTrust",
            "OnetrustActiveGroups",
            "OptanonWrapper",
            "Optanon",
            "OnetrustCategories",
            "OneTrustLoaded",
            "OptanonConsent",
        ],
    },
    "TrustArc": {
        "Ids": [
            "truste-consent-track",
            "truste-consent-content",
            "truste-consent-buttons",
            "truste-show-consent",
            "trustarcNoticeFrame",
            "consent-banner",
            "consent_blackbar",
            "teconsent",
            "tap-notification",
            "trustarc-banner-container",
            "pdynamicbutton",
            "truste-eu-cookie-message",
        ],
        "Classes": [
            "trustarc-banner",
            "truste_box_overlay",
            "truste_popframe",
            "trustarc-banner-container",
            "truste-consent-buttons",
        ],
        "Scripts": [
            "trustarc.com",
            "consent.trustarc.com",
            "truste.com",
            "notice.truste.com",
            "static.trustarc.com",
        ],
        "Globals": [
            "truste",
            "TRUSTe",
            "truste_prefs",
            "PrivacyManagerAPI",
            "truste_cma_settings",
        ],
    },
    "Cookiebot": {
        "Ids": [
            "CookiebotWidget",
            "CybotCookiebotDialog",
            "CybotCookiebotDialogBodyLevelButtonAccept",
            "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "CookiebotDeclaration",
        ],
        "Classes": ["CookieConsent", "CybotCookiebotDialogActive"],
        "Scripts": ["cookiebot.com/uc.js", "consent.cookiebot.com"],
        "Globals": ["Cookiebot", "CookiebotDialog"],
    },
    "Didomi": {
        "Ids": ["didomi-host", "didomi-notice", "didomi-popup"],
        "Classes": ["didomi-consent-popup", "didomi-notice-banner"],
        "Scripts": ["didomi.io", "sdk.privacy-center.org"],
        "Globals": ["didomiOnReady", "didomiState", "Didomi"],
    },
    "Usercentrics": {
        "Ids": ["usercentrics-root", "usercentrics-cmp-ui"],
        "Classes": [],
        "Scripts": ["usercentrics.js", "app.usercentrics.eu"],
        "Globals": ["UC_UI", "usercentrics", "UC_UI_CMP2"],
    },
    "Osano": {
        "Ids": [],
        "Classes": [
            "osano-cm-window",
            "osano-cm-dialog",
            "osano-cm-widget",
            "osano-cm-button",
            "osano-cm-buttons",
        ],
        "Scripts": ["cmp.osano.com", "cdn.osano.com"],
        "Globals": ["Osano"],
    },
    "Sourcepoint": {
        "Ids": [],
        # id is generated per-message (sp_message_container_<id>) so it's
        # matched with a startswith check in cmp_identifier, not exact match.
        "IdPrefixes": ["sp_message_container"],
        "Classes": [],
        "Scripts": ["cdn.privacy-mgmt.com", "wrapperMessagingWithoutDetection.js"],
        "Globals": ["_sp_", "_sp_queue"],
    },
    "Quantcast Choice": {
        "Ids": ["qc-cmp2-container", "qc-cmp2-ui"],
        "Classes": [],
        "Scripts": ["quantcast.mgr.consensu.org", "cmp.quantcast.com"],
        "Globals": ["__cmpLocator"],
    },
    "CookieYes": {
        "Ids": [],
        "Classes": ["cky-banner-element", "cky-consent-bar", "cky-btn-accept"],
        "Scripts": ["cdn-cookieyes.com"],
        "Globals": ["getCkyConsent"],
    },
    "IAB TCF (Unspecified Vendor)": {
        # Generic fallback for any IAB TCF v2-compliant CMP that doesn't match
        # a specific vendor above — flags "some TCF-compliant CMP is present"
        # rather than mislabeling it as one specific product.
        "Ids": [],
        "Classes": [],
        "Scripts": [],
        "Globals": ["__tcfapi", "__tcfapiLocator"],
    },
    "Microsoft Custom": {
        # mscc verified directly from a real scan (user's own cmp_debug.log).
        "Ids": [],
        "Classes": [],
        "Scripts": [],
        "Globals": ["mscc"],
    },
    "Amazon Custom": {
        # sp-cc verified directly from a real scan (user's own cmp_debug.log).
        "Ids": ["sp-cc"],
        "Classes": [],
        "Scripts": [],
        "Globals": [],
    },
}

# Flat list of every registry "Ids" + "IdPrefixes" entry, used to build a
# single CSS selector so the scanner can wait for *any* known banner to
# appear instead of always sleeping the full fixed timeout.
_AllKnownIds = [Id for Sigs in CmpRegistry.values() for Id in Sigs.get("Ids", [])]
BannerWaitSelector = ", ".join(f"#{Id}" for Id in _AllKnownIds)

# Generic fallback keywords: flag anything with an obviously consent-related
# id/class even if it doesn't match a known CMP signature.
GenericBannerKeywords = ["cookie", "consent", "privacy-banner", "notice-banner"]

# ---------------------------------------------------------------------------
# Privacy policy detection
# ---------------------------------------------------------------------------
# Maps domains that host privacy policies on a site's behalf to the readable
# provider name. If a policy resolves to one of these domains instead of the
# site's own domain, the policy is flagged as third-party/vendor hosted.
ThirdPartyPolicyHosts = {
    "privacyportal.onetrust.com": "OneTrust Privacy Portal",
    "onetrust.com": "OneTrust",
    "trustarc.com": "TrustArc",
    "privacy.truste.com": "TrustArc (TRUSTe)",
    "iubenda.com": "iubenda",
    "termly.io": "Termly",
    "termsfeed.com": "TermsFeed",
    "privacypolicies.com": "PrivacyPolicies.com",
    "freeprivacypolicy.com": "FreePrivacyPolicy.com",
    "websitepolicies.com": "WebsitePolicies",
    "cookieyes.com": "CookieYes",
    "secureprivacy.ai": "Secure Privacy",
    "getterms.io": "GetTerms",
    "docracy.com": "Docracy",
}

# Anchor text / href patterns that indicate a link points to a privacy policy.
PrivacyLinkPattern = re.compile(
    r"privacy[\s\-]?(policy|notice|statement)|data[\s\-]?protection", re.IGNORECASE
)

# Words that show up in nav/footer link lists rather than an actual company
# name; used both to reject regex captures that ran into surrounding menu
# text, and as lookahead stop-words so the regex doesn't swallow them in the
# first place (e.g. "Trustpilot A/S" not "Trustpilot A/S Terms Privacy Dispute").
OwnerStopWords = (
    r"All|Rights|Reserved|Terms|Privacy|Cookie|Dispute|Polic(?:y|ies)|Notice|"
    r"Statement|Center|Centre|Settings|Preferences|Conditions|Agreement|"
    r"Sitemap|Accessibility|Help|Contact|FAQ|Legal|Use|Or|Its|Affiliated|Companies"
)

# Pulls a likely "owner" name out of a policy page's footer/copyright text,
# e.g. "\u00a9 2026 Example Corp, Inc. All rights reserved." -> "Example Corp, Inc."
# Each word is checked against OwnerStopWords via lookahead as it's consumed,
# so the match stops before running into a trailing nav list on the same line.
CopyrightOwnerPattern = re.compile(
    r"(?:\u00a9|©|Copyright)\s*(?:\d{4}[\u2013\-]?\d{0,4})?\s*,?\s*"
    r"((?:(?:(?!(?:"
    + OwnerStopWords
    + r")\b)[A-Z][\w&.,'\-/]*|and|of|&|the)\s*){1,7})",
    re.MULTILINE,
)

OwnerCandidateBlocklist = {
    Word.lower() for Word in re.findall(r"[A-Za-z]+", OwnerStopWords)
}

# Suffixes stripped when comparing a detected owner name against the site's
# own domain, so "Example Corp, Inc." and "example.com" can be matched up.
CompanySuffixPattern = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|gmbh|s\.?a\.?|a/s|group|plc|holdings|company)\b\.?",
    re.IGNORECASE,
)

# Recognizes something that looks like a bare domain or URL, e.g.
# "stackoverflow.com" or "https://example.com/path" — used to detect a
# headerless input file where the first data row got misread as a header.
DomainLikePattern = re.compile(r"^(https?://)?[\w\-]+(\.[\w\-]+)+(/\S*)?$")

UrlColumnCandidates = ["websiteurl", "url", "website", "domain", "site"]
