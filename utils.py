"""utils.py — small stateless helpers shared across modules."""

import logging
import re
from urllib.parse import urlparse

import files.config as config


def SetupLogging():
    """Configure root logging once: console + scan.log file, same format everywhere."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.SCAN_LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def GetWebsiteName(Url):
    """Extract a bare domain (no scheme, no 'www.') from a URL, e.g. 'discord.com'."""
    if not Url:
        return ""
    Candidate = Url if "://" in Url else f"https://{Url}"
    try:
        Netloc = urlparse(Candidate).netloc
    except ValueError:
        return Url
    return Netloc[4:] if Netloc.startswith("www.") else Netloc


def NormalizeUrl(Url):
    """Prefix a bare domain with https:// so Playwright can navigate to it."""
    Url = (Url or "").strip()
    if not Url:
        return Url
    return Url if "://" in Url else f"https://{Url}"


def DedupeUrls(Urls):
    """
    Drop redundant entries that are the same site once you ignore scheme/
    'www.' differences (e.g. "https://www.tata.com" and "tata.com"), keeping
    the first occurrence's original formatting. Returns (deduped_list,
    number_removed) so the caller can log what happened.
    """
    Seen = set()
    Deduped = []
    for Url in Urls:
        Key = GetWebsiteName(Url)
        if Key and Key not in Seen:
            Seen.add(Key)
            Deduped.append(Url)
    return Deduped, len(Urls) - len(Deduped)


def IsPlausibleOwnerName(Candidate):
    """Reject regex captures that are really just a run of nav-link text."""
    if not Candidate:
        return False
    Words = [W.strip(".,'-") for W in Candidate.split()]
    Words = [W for W in Words if W]
    if not (1 <= len(Words) <= 6):
        return False
    if any(Word.lower() in config.OwnerCandidateBlocklist for Word in Words):
        return False
    return True


def NormalizeCompanyName(Name):
    """Strip legal suffixes and punctuation so names can be fuzzy-compared."""
    if not Name:
        return ""
    Name = config.CompanySuffixPattern.sub("", Name)
    return re.sub(r"[^a-z0-9]", "", Name.lower())


def CheckOwnerMatchesSite(SiteDomain, OwnerName):
    """
    Flag whether the detected policy owner plausibly matches the site being
    scanned. A mismatch is a useful signal on its own (subsidiary, misconfigured
    template, or a policy that was never actually customized for the domain)
    rather than something to silently resolve.
    """
    if not OwnerName or OwnerName == "Unknown":
        return "Unknown"
    SiteCore = re.sub(r"[^a-z0-9]", "", (SiteDomain or "").split(".")[0].lower())
    SiteCore = re.sub(r"^the", "", SiteCore) or SiteCore
    OwnerNorm = NormalizeCompanyName(OwnerName)
    if not SiteCore or not OwnerNorm:
        return "Unknown"
    if SiteCore in OwnerNorm or OwnerNorm in SiteCore:
        return "Yes"
    # Looser fallback: any individual word (4+ chars) from the owner name
    # shares a stem with the domain, e.g. "Guardian" in "theguardian.com".
    OwnerWords = [W for W in re.findall(r"[a-z0-9]+", OwnerName.lower()) if len(W) >= 4]
    if any(Word in SiteCore or SiteCore in Word for Word in OwnerWords):
        return "Yes"
    return "No (verify manually)"
