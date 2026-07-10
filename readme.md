# Async CMP Compliance Scanner

A concurrent Python tool that uses **Playwright** to crawl a list of websites, detect which Consent Management Platform (CMP) each one runs (OneTrust, Cookiebot, TrustArc, Didomi, etc.), and log the results to a live Google Sheet as well as a local Excel backup.

---

## How it works

The crawler runs on `asyncio`. Instead of blocking on slow page loads or network requests, it spins up multiple browser workers concurrently, capped by a semaphore so you don't exhaust memory or get rate-limited.

```
                  +-------------------+
                  |  input_urls.xlsx  |
                  +---------+---------+
                            |
                            v
                  +---------+---------+
                  |    MainCrawler    |
                  +---------+---------+
                            |
        (Semaphore limits concurrency to 5)
                            |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
  +-----------+       +-----------+       +-----------+
  | Worker 1  |       | Worker 2  |       | Worker 3  |
  | (browser) |       | (browser) |       | (browser) |
  +-----+-----+       +-----+-----+       +-----+-----+
        |                   |                   |
        +-------------------+-------------------+
                            |
              +-------------+-------------+
              |                           |
              v                           v
      +---------------+           +---------------+
      | Cloud stream  |           | Local backup  |
      | (gspread API) |           | (pandas)      |
      +-------+-------+           +-------+-------+
              |                           |
              v                           v
      +---------------+           +---------------+
      | Google Sheets |           | output_results|
      +---------------+           +---------------+
```

### Detection logic

For each page, the scanner checks four signal types to identify a CMP:

1. DOM element **IDs**
2. **CSS classes**
3. Injected **script sources**
4. Global **`window` variables**

If none of the known signatures match, a fallback heuristic scans for common consent-related keywords to flag likely custom or unrecognized banners.

---

## Setup

Requires Python 3.9+.

### 1. Install dependencies

```bash
cd ccm-scanner-pipeline

python -m venv venv

# macOS/Linux
source venv/bin/activate
# Windows (cmd)
venv\Scripts\activate.bat
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

pip install playwright pandas openpyxl gspread google-auth
playwright install chromium
```

### 2. Prepare the input file

Create `input_urls.xlsx` in the project root with a single column named `Website URL`:

| Website URL            |
| ---------------------- |
| https://www.amazon.com |
| https://www.ibm.com    |
| https://www.didomi.io  |

---

## Google Sheets integration (optional)

To stream live results to a Google Sheet, you'll need a service account.

**1. Create a Google Cloud project**

- Go to the [Google Cloud Console](https://console.cloud.google.com/)
- Create a new project (e.g. `CMP-Data-Compliance-Auditor`)

**2. Enable the required APIs**

- Enable the **Google Sheets API**
- Enable the **Google Drive API**

**3. Create a service account**

- Go to **APIs & Services → Credentials → Create Credentials → Service Account**
- Give it a name (e.g. `crawler-agent`)
- Grant it the **Editor** role

**4. Download the credentials**

- Open the service account, go to **Keys → Add Key → Create new key**
- Choose **JSON** and download it
- Move the downloaded file into your project directory and rename it to `credentials.json`

**5. Share your sheet with the service account**

- Create a Google Sheet named exactly `Crawler Dashboard`
- Open `credentials.json` and copy the `client_email` value (ends in `.gserviceaccount.com`)
- In the Sheet, click **Share**, paste that email, give it **Editor** access, uncheck "Notify people," and share

> **Note:** If the service account email isn't added to the sheet, you'll get `APIError [403]: PERMISSION_DENIED`. The scanner catches this automatically and falls back to writing results locally to `output_results.xlsx` instead of crashing.

---

## Running it

```bash
python main.py
```

### Output files

| File                                       | Description                                                            |
| ------------------------------------------ | ---------------------------------------------------------------------- |
| **Crawler Dashboard** (Google Sheet) | Live results, updated as each site finishes scanning                   |
| **output_results.xlsx**              | Local backup written at the end of the run                             |
| **scan.log**                         | Runtime log: concurrency, timeouts, access errors, timing              |
| **cmp_debug.log**                    | Detailed log of every matched ID, class, script, and variable per site |

---

## Code structure

| Function                                                       | Purpose                                                                                                                                    |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `GetWebsiteName(url)`                                        | Extracts a clean domain name from a URL (strips`www.`, query params, etc.) for readable reports                                          |
| `AppendToDebugLog(entry)`                                    | Writes detailed match data to`cmp_debug.log` without cluttering the main dashboard                                                       |
| `AnalyzePageEvidence(page, url)`                             | Core detection logic — injects JS into the page to collect DOM/script/variable evidence and matches it against the CMP signature registry |
| `ScanSingleWebsite(browser, url, semaphore, dashboard)`      | Manages a single browser tab's lifecycle; wraps everything in try/except so one slow or broken site doesn't stall the whole run            |
| `MainCrawler(input_path, output_path, max_concurrent_sites)` | Orchestrates the whole run: reads input, authenticates with Google, launches workers, and writes final output                              |
