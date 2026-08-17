"""
cmp_identifier.py — turns raw page signals (banner_detector.py's output) into
a CmpEvidence verdict. Checks in a fixed priority order (ids -> classes ->
scripts -> globals) and stops at the first hit, so the "Reason" column always
reflects the strongest signal found rather than an arbitrary one.

Never guesses: if nothing in the registry matches, falls back to a generic
consent-keyword heuristic, and if even that finds nothing the page is simply
reported as no banner detected.
"""

import files.config as config
from files.models import CmpEvidence


def _MatchIds(FoundIds, Signature):
    # Defensive: only hashable strings can go into a set — anything else
    # (an unexpected object from a page overriding a native property) is
    # silently dropped here rather than crashing the whole match.
    FoundSet = {Item for Item in FoundIds if isinstance(Item, str)}
    for KnownId in Signature.get("Ids", []):
        if KnownId in FoundSet:
            return KnownId
    for Prefix in Signature.get("IdPrefixes", []):
        for FoundId in FoundIds:
            if isinstance(FoundId, str) and FoundId.startswith(Prefix):
                return FoundId
    return None


def _MatchClasses(FoundClasses, Signature):
    FoundSet = {Item for Item in FoundClasses if isinstance(Item, str)}
    for KnownClass in Signature.get("Classes", []):
        if KnownClass in FoundSet:
            return KnownClass
    return None


def _MatchScripts(FoundScripts, Signature):
    for KnownScript in Signature.get("Scripts", []):
        for FoundScript in FoundScripts:
            if isinstance(FoundScript, str) and KnownScript in FoundScript:
                return KnownScript
    return None


def _MatchGlobals(FoundGlobals, Signature):
    FoundSet = {Item for Item in FoundGlobals if isinstance(Item, str)}
    for KnownGlobal in Signature.get("Globals", []):
        if KnownGlobal in FoundSet:
            return KnownGlobal
    return None


def IdentifyCmp(Url, Title, Signals):
    """
    Signals is the dict returned by banner_detector.CollectPageSignals.
    Returns a populated CmpEvidence. Match priority per vendor: Ids, then
    Classes, then Scripts, then Globals — DOM ids are the most specific
    signal a CMP can leave, so they're checked first.
    """
    Evidence = CmpEvidence(Url=Url, Title=Title or "")

    for VendorName, Signature in config.CmpRegistry.items():
        IdHit = _MatchIds(Signals["Ids"], Signature)
        if IdHit:
            Evidence.BannerPresent = "Yes"
            Evidence.CmpDetected = VendorName
            Evidence.Reason = f"ID Match ({IdHit})"
            Evidence.FoundIds = [
                I
                for I in Signals["Ids"]
                if isinstance(I, str) and I in set(Signature.get("Ids", []))
            ]
            return Evidence

        ClassHit = _MatchClasses(Signals["Classes"], Signature)
        if ClassHit:
            Evidence.BannerPresent = "Yes"
            Evidence.CmpDetected = VendorName
            Evidence.Reason = f"Class Match ({ClassHit})"
            return Evidence

        ScriptHit = _MatchScripts(Signals["Scripts"], Signature)
        if ScriptHit:
            Evidence.BannerPresent = "Yes"
            Evidence.CmpDetected = VendorName
            Evidence.Reason = f"Script Match ({ScriptHit})"
            return Evidence

        GlobalHit = _MatchGlobals(Signals["Globals"], Signature)
        if GlobalHit:
            Evidence.BannerPresent = "Yes"
            Evidence.CmpDetected = VendorName
            Evidence.Reason = f"Global Window Variable Match ({GlobalHit})"
            Evidence.FoundGlobals = [GlobalHit]
            return Evidence

    # No registry vendor matched — fall back to a generic "something
    # consent-related exists" heuristic before giving up entirely.
    for FoundId in Signals["Ids"]:
        if not isinstance(FoundId, str):
            continue
        LowerId = FoundId.lower()
        if any(Keyword in LowerId for Keyword in config.GenericBannerKeywords):
            Evidence.BannerPresent = "Yes"
            Evidence.CmpDetected = "Unknown"
            Evidence.Reason = f"Generic DOM match (ID: {FoundId})"
            return Evidence

    Evidence.BannerPresent = "No"
    Evidence.CmpDetected = "N/A"
    Evidence.Reason = "None"
    return Evidence
