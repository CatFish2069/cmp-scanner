# CMP Compliance Scanner

Crawls a list of websites and reports, per site:

- whether a cookie/consent banner is present, and which CMP (Consent
  Management Platform) is running it
- the site's privacy policy link, who actually hosts it, and whether the
  entity named in the policy text plausibly matches the site itself

Built to run over lists in the thousands without babysitting: it streams
results to disk as it goes, skips URLs it already finished on a rerun, and
recycles its browser periodically so memory doesn't creep up over a long run.

## Setup

```powershell
# Windows PowerShell — create and activate a virtual environment
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
python -m venv venv
.\venv\Scripts\Activate.ps1
```

```bash
# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

```bash
pip install -r requirements.txt --break-system-packages   # or use a venv
playwright install chromium
```

Put your URLs in `input_urls.csv` or `input_urls.xlsx` (whichever you have —
the scanner checks for a CSV first, then Excel; you can also pass an explicit
path as `python main.py path/to/file.csv`). Either format accepts a column
named `Website URL` / `URL` / `Website` / `Domain` / `Site` (case/space
insensitive), or just a single column of bare domains with no header at
all. Duplicate rows (same domain, different `www.`/scheme formatting) are
detected and skipped automatically before scanning starts. Optionally drop a `credentials.json` service-account file next to
`main.py` (see `credentials.example.json` for the expected shape) to also
mirror results into a Google Sheet named `Crawler Dashboard`; if it's
missing the scanner logs a warning and continues with CSV/Excel output
only. The service account needs both the Sheets and Drive API scopes
enabled (Drive is required just to look the spreadsheet up by name), and
the sheet itself must be shared with the service account's `client_email`
address. **Never commit your real `credentials.json`** — it's already
covered by `.gitignore`.

```bash
python main.py
```

## File layout

| File                  | Responsibility                                                                                       |
|------------------------|-------------------------------------------------------------------------------------------------------|
| `config.py`            | All tunables in one place: paths, concurrency/timeouts, the CMP signature registry, regex patterns    |
| `models.py`            | Typed result shapes (`ScanResult`, `CmpEvidence`, `PolicyEvidence`) shared across modules              |
| `utils.py`             | Small stateless helpers: domain parsing, owner-name normalization/matching, logging setup             |
| `banner_detector.py`   | Pulls raw signals (DOM ids/classes, script sources, window globals) off a live page — no CMP logic     |
| `cmp_identifier.py`    | Matches those signals against `config.CmpRegistry` to name the CMP, in ids → classes → scripts → globals priority |
| `privacy_policy.py`    | Finds the privacy policy link, inspects who hosts it, and extracts/validates the named owner            |
| `browser_manager.py`   | Owns the shared Chromium instance; recycles it every N scans to bound memory growth                    |
| `scanner.py`           | Scans one site end to end, with a retry on timeout/failure                                              |
| `excel_handler.py`     | Input loading (header-quirk tolerant), streaming CSV writer with resume support, Google Sheets setup    |
| `main.py`              | Entry point — wires the above together and runs the batch                                               |

## CMP signature registry

Every signature in `config.CmpRegistry` is sourced from vendor documentation
or real implementation snippets — nothing is guessed, and a page that
matches nothing in the registry is reported as `Unknown`, never assigned a
best-guess vendor. Currently covers: **OneTrust**, **TrustArc**, Cookiebot,
Didomi, Usercentrics, Osano, Sourcepoint, Quantcast Choice, CookieYes, a
generic IAB TCF v2 fallback (`__tcfapi`) for any TCF-compliant CMP that
doesn't match a specific vendor, plus two site-specific globals (Microsoft's
`mscc`, Amazon's `sp-cc`) confirmed from real scans. OneTrust and TrustArc
carry the deepest coverage (12-15 signatures each across ids/classes/
scripts/globals) since they're the two most commonly encountered.

If none of the known signatures match, a fallback heuristic scans for
generic consent-related keywords (`cookie`, `consent`, etc.) in element ids
to flag likely custom/unrecognized banners.

## Privacy policy check

For every site, the scanner finds the in-page privacy policy/notice link,
opens it, and reports:

- **Privacy Policy URL** — the final URL after redirects
- **Privacy Policy Host** — the domain actually serving it
- **Third-Party Hosted Policy** — flagged when it's served from a known
  vendor domain (OneTrust Privacy Portal, TrustArc, iubenda, Termly,
  PrivacyPolicies.com, CookieYes, etc.) instead of the site's own domain
- **Detected Policy Owner** — the entity named in the policy, found via (in
  order) `og:site_name`/`application-name` meta tags, a copyright/footer
  line checked at both the top and bottom of the page, or the `<title>` tag
- **Owner Matches Site** — `Yes` / `No (verify manually)` / `Unknown`,
  comparing the detected owner against the site's own domain. A `No` is a
  useful signal on its own — a subsidiary, an un-customized template, or a
  policy that names a different entity than the site itself.

## Designed for 10K-site runs

- **Streaming, resumable output.** Results are written to
  `output_results.csv` one row at a time as each site finishes, not held in
  memory until the end. Rerunning `python main.py` on the same input skips
  any URL already recorded with `Status/Error = Success`, so an interrupted
  10,000-site run can just be restarted.
- **Bounded concurrency with a fast early-exit wait.** `MAX_CONCURRENT_SITES`
  (default 15) browser tabs run at once. Instead of a flat fixed sleep per
  site, the scanner waits for any known CMP banner selector to appear (up to
  `BANNER_WAIT_TIMEOUT_MS`), exiting early the moment it shows up rather than
  always waiting the full timeout.
- **Browser recycling that doesn't race concurrent scans.** The shared
  Chromium instance is relaunched every `BROWSER_RECYCLE_EVERY` scans
  (default 300) to bound memory growth. New scans switch to the fresh
  instance immediately; the retired one keeps serving whatever contexts it
  already handed out and only closes itself once they've all finished —
  never mid-operation.
- **Self-healing on a dead browser/driver.** If Chromium (or the whole
  Playwright driver process) crashes outright, the next scan attempt
  detects the dead connection and transparently relaunches everything
  instead of every subsequent site failing in a cascade.
- **Retry on transient failure.** A timed-out or failed scan gets one retry
  with a longer timeout before being recorded as an error — a meaningful
  fraction of failures at this scale are just network hiccups, not broken
  sites.
- **Clean interruption.** Ctrl+C exits with a plain "progress is saved,
  rerun to resume" message instead of a raw traceback, and known-benign
  Windows shutdown noise (proactor socket warnings from killing Chromium's
  pipes mid-interrupt) is suppressed rather than left to clutter the log.
- **A final `output_results.xlsx`** is built from the CSV once the run
  finishes, for convenience in Excel/Sheets.

## Known limitation: geo-gated banners

Consent banners are frequently shown or hidden based on the crawler's real
IP-based geolocation, not request headers — a site that's fully GDPR-gated
for EU visitors may load its CMP script but never render/trigger anything
for a non-EU crawler IP. A `Cookie Banner Available = No` result means no
known CMP signature was found on the page as loaded from wherever the
scanner is running, not necessarily that the site has no CMP at all.
