"""
privacy_policy.py — finds a site's privacy policy link, opens it, and works
out (a) who actually hosts it and (b) which entity it names as the owner,
flagging whether that owner plausibly matches the site being scanned.
"""

import logging
import re
from urllib.parse import urljoin

import files.config as config
from files.models import PolicyEvidence
from files.utils import GetWebsiteName, IsPlausibleOwnerName, CheckOwnerMatchesSite


async def FindPrivacyPolicyLink(Page, BaseUrl):
    """Scan in-page anchors for the first link that looks like a privacy policy/notice."""
    try:
        Anchors = await Page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).map(a => ({text: a.innerText || '', href: a.href}))"
        )
    except Exception as Ex:
        logging.debug(f"Failed collecting anchors on {BaseUrl}: {Ex}")
        return None

    for Anchor in Anchors:
        AnchorText = Anchor.get("text", "") or ""
        AnchorHref = Anchor.get("href", "") or ""
        if config.PrivacyLinkPattern.search(
            AnchorText
        ) or config.PrivacyLinkPattern.search(AnchorHref):
            return urljoin(BaseUrl, AnchorHref)

    return None


async def ExtractPolicyOwner(PolicyPage):
    """
    Work out which entity a privacy policy page belongs to, trying the most
    reliable signal first:
      1. og:site_name / application-name meta tags
      2. a copyright line, checked at both the top and bottom of the page
         (footers sit at the end of very long legal-text pages)
      3. the <title> tag, stripped of a trailing "- Privacy Policy" suffix
    Falls back to "Unknown" rather than guessing.
    """
    try:
        MetaOwner = await PolicyPage.evaluate("""() => {
            const og = document.querySelector('meta[property="og:site_name"]');
            if (og && og.content && og.content.trim()) return og.content.trim();
            const appName = document.querySelector('meta[name="application-name"]');
            if (appName && appName.content && appName.content.trim()) return appName.content.trim();
            return null;
        }""")
        if MetaOwner and IsPlausibleOwnerName(MetaOwner):
            return MetaOwner

        BodyText = (
            await PolicyPage.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            or ""
        )
        # Footers sit at the very end of long legal-text pages, so check both
        # ends rather than only the first few thousand characters.
        for Window in (BodyText[:4000], BodyText[-6000:]):
            for OwnerMatch in config.CopyrightOwnerPattern.finditer(Window):
                Candidate = re.sub(r"\s+", " ", OwnerMatch.group(1)).strip(" .,-|")
                if IsPlausibleOwnerName(Candidate):
                    return Candidate

        TitleText = (await PolicyPage.title()) or ""
        TitleParts = re.split(
            r"\s*[-|:\u2013]\s*(?:Privacy|Cookie)", TitleText, flags=re.IGNORECASE
        )
        if (
            TitleParts
            and TitleParts[0].strip()
            and TitleParts[0].strip() != TitleText.strip()
        ):
            Candidate = TitleParts[0].strip()
            if IsPlausibleOwnerName(Candidate):
                return Candidate

    except Exception as Ex:
        logging.debug(f"Failed extracting policy owner: {Ex}")

    return "Unknown"


async def InspectPrivacyPolicy(Context, SiteUrl, PolicyLink):
    """
    Open the privacy policy link in its own tab, follow redirects, and work out:
    - which domain actually hosts the policy (own domain vs a third-party provider)
    - which company/entity the policy text names as the owner
    - whether that owner plausibly matches the site being scanned
    """
    PolicyEvidenceResult = PolicyEvidence(PrivacyPolicyUrl=PolicyLink or "Not Found")

    if not PolicyLink:
        return PolicyEvidenceResult

    PolicyPage = await Context.new_page()
    try:
        await PolicyPage.goto(
            PolicyLink,
            timeout=config.POLICY_GOTO_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        FinalUrl = PolicyPage.url
        PolicyEvidenceResult.PrivacyPolicyUrl = FinalUrl

        SiteDomain = GetWebsiteName(SiteUrl)
        PolicyDomain = GetWebsiteName(FinalUrl)
        PolicyEvidenceResult.PrivacyPolicyHost = PolicyDomain

        MatchedProvider = next(
            (
                Name
                for Domain, Name in config.ThirdPartyPolicyHosts.items()
                if Domain in PolicyDomain
            ),
            None,
        )
        if MatchedProvider:
            PolicyEvidenceResult.ThirdPartyHosted = f"Yes ({MatchedProvider})"
        elif PolicyDomain and SiteDomain and PolicyDomain != SiteDomain:
            PolicyEvidenceResult.ThirdPartyHosted = (
                f"Yes (Unknown host: {PolicyDomain})"
            )
        else:
            PolicyEvidenceResult.ThirdPartyHosted = "No"

        DetectedOwner = await ExtractPolicyOwner(PolicyPage)
        PolicyEvidenceResult.DetectedPolicyOwner = DetectedOwner
        PolicyEvidenceResult.OwnerMatchesSite = CheckOwnerMatchesSite(
            SiteDomain, DetectedOwner
        )

    except Exception as Ex:
        logging.debug(f"Failed inspecting privacy policy at {PolicyLink}: {Ex}")
        PolicyEvidenceResult.PrivacyPolicyHost = "Error"
        PolicyEvidenceResult.ThirdPartyHosted = "Error"
    finally:
        try:
            await PolicyPage.close()
        except Exception as Ex:
            logging.debug(f"Policy page close failed (already closed?): {Ex}")

    return PolicyEvidenceResult
