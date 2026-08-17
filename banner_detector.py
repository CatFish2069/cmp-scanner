"""
banner_detector.py — collects raw page signals for the CMP identifier to
match against config.CmpRegistry. Kept separate from cmp_identifier.py so
"what did the page contain" and "what CMP does that imply" don't get tangled.
"""

import logging

import files.config as config

# Every global variable name any registry entry cares about, flattened once
# so a single page.evaluate call can check them all in one round trip instead
# of one call per candidate (which would be far too slow at 10K-site scale).
_AllGlobalNames = sorted(
    {
        Global
        for Signature in config.CmpRegistry.values()
        for Global in Signature.get("Globals", [])
    }
)


def _StringsOnly(Items):
    """
    Coerce a JS-evaluate result to a clean list of strings, dropping anything
    that isn't one. Most pages return plain strings for ids/classes/scripts,
    but a page whose own JS overrides a native property (id, classList, etc.)
    can occasionally hand back an object instead — without this, that single
    unexpected entry crashes every set()-based match in cmp_identifier.py for
    the whole site.
    """
    Cleaned = [Item for Item in (Items or []) if isinstance(Item, str)]
    if len(Cleaned) != len(Items or []):
        logging.debug(
            f"Dropped {len(Items) - len(Cleaned)} non-string signal entrie(s)."
        )
    return Cleaned


async def CollectPageSignals(Page):
    """
    Single round trip into the page to gather everything the identifier needs:
    - every element id present in the DOM
    - every class token present in the DOM
    - every <script src="..."> value
    - which of the known CMP global variable names exist on window
    Returns a dict; never raises — a failed evaluate just yields empty signals
    so one broken page can't take down the whole scan.
    """
    Signals = {"Ids": [], "Classes": [], "Scripts": [], "Globals": []}

    try:
        DomSignals = await Page.evaluate("""() => {
                const ids = Array.from(document.querySelectorAll('[id]')).map(el => el.id);
                const classes = Array.from(document.querySelectorAll('[class]'))
                    .flatMap(el => Array.from(el.classList));
                const scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
                return { ids, classes, scripts };
            }""")
        Signals["Ids"] = _StringsOnly(DomSignals.get("ids", []))
        Signals["Classes"] = _StringsOnly(DomSignals.get("classes", []))
        Signals["Scripts"] = _StringsOnly(DomSignals.get("scripts", []))
    except Exception as Ex:
        logging.debug(f"Signal collection (DOM) failed: {Ex}")

    try:
        FoundGlobals = await Page.evaluate(
            "(names) => names.filter(n => typeof window[n] !== 'undefined')",
            _AllGlobalNames,
        )
        Signals["Globals"] = _StringsOnly(FoundGlobals)
    except Exception as Ex:
        logging.debug(f"Signal collection (globals) failed: {Ex}")

    return Signals


async def WaitForLikelyBanner(Page):
    """
    Early-exit wait: if any known CMP banner id shows up within the timeout,
    stop waiting immediately instead of always sleeping the full duration.
    Falls back to a flat short sleep if the selector never matches (banner
    may be a custom implementation not in the registry, or geo-suppressed).
    """
    if config.BannerWaitSelector:
        try:
            await Page.wait_for_selector(
                config.BannerWaitSelector, timeout=config.BANNER_WAIT_TIMEOUT_MS
            )
            return
        except Exception:
            pass  # no known banner appeared in time — fall through to flat wait

    await Page.wait_for_timeout(2500)
