"""
excel_handler.py — all file/sheet I/O:
- LoadUrlTargets: robust input reading (header-name variations, headerless files)
- ResultWriter: streams rows to CSV as they complete (crash-safe, resumable),
  and produces the final .xlsx copy at the end
- Google Sheets dashboard setup (best-effort; the run continues without it)
"""

import csv
import logging
import os

import pandas as pd

import files.config as config


def _ReadTable(InputPath, **ReadKwargs):
    """Read a spreadsheet-like file regardless of whether it's .csv or .xlsx/.xls."""
    Extension = os.path.splitext(InputPath)[1].lower()
    if Extension == ".csv":
        return pd.read_csv(InputPath, **ReadKwargs)
    return pd.read_excel(InputPath, **ReadKwargs)


def ResolveInputPath(PreferredPath=None):
    """
    Find the input file to scan, accepting either CSV or Excel. Checks, in
    order: an explicitly given path, then config.INPUT_PATH_CANDIDATES
    (input_urls.csv, input_urls.xlsx, ...). Raises FileNotFoundError listing
    everywhere it looked if nothing is found.
    """
    Candidates = (
        [PreferredPath] if PreferredPath else list(config.INPUT_PATH_CANDIDATES)
    )
    for Candidate in Candidates:
        if Candidate and os.path.exists(Candidate):
            return Candidate
    raise FileNotFoundError(
        f"No input file found. Looked for: {', '.join(Candidates)}. "
        f"Provide either a .csv or .xlsx file with a URL column."
    )


def LoadUrlTargets(InputPath):
    """
    Read the input file (CSV or Excel — detected from the extension) and
    return (url_list, column_used_for_logging). Tolerates a few common
    formatting quirks: a header with different wording/casing/spacing
    ("URL", "Website", "Domain Url "...), or no header row at all (a single
    column of bare domains where the first URL landed in the header).
    Returns (None, columns_found) if nothing usable is found.
    """
    InputDataFrame = _ReadTable(InputPath)
    InputDataFrame.columns = [str(Col).strip() for Col in InputDataFrame.columns]
    NormalizedColumns = {
        Col.lower().replace(" ", ""): Col for Col in InputDataFrame.columns
    }

    MatchedColumn = next(
        (
            NormalizedColumns[Candidate]
            for Candidate in config.UrlColumnCandidates
            if Candidate in NormalizedColumns
        ),
        None,
    )
    if MatchedColumn:
        return (
            InputDataFrame[MatchedColumn].dropna().astype(str).tolist(),
            MatchedColumn,
        )

    if InputDataFrame.shape[1] == 1:
        SoleColumnName = InputDataFrame.columns[0]
        if config.DomainLikePattern.match(SoleColumnName):
            RawDataFrame = _ReadTable(InputPath, header=None)
            return (
                RawDataFrame[0].dropna().astype(str).tolist(),
                "(headerless single column)",
            )
        logging.info(
            f"Unrecognized column name '{SoleColumnName}' — using it as the URL source anyway."
        )
        return (
            InputDataFrame[SoleColumnName].dropna().astype(str).tolist(),
            SoleColumnName,
        )

    return None, list(InputDataFrame.columns)


class ResultWriter:
    """
    Streams scan results to CSV one row at a time so a crash at site 9,000 of
    10,000 loses nothing already scanned. On startup, reads any existing
    output CSV so a rerun can skip URLs already completed (resume support).
    """

    def __init__(self, CsvPath=config.OUTPUT_CSV_PATH, Dashboard=None):
        self.CsvPath = CsvPath
        self.Dashboard = Dashboard
        self._RowsWrittenSinceFlush = 0
        self.AlreadyDone = self._LoadCompletedUrls()

        FileIsNew = not os.path.exists(self.CsvPath)
        self._File = open(self.CsvPath, "a", newline="", encoding="utf-8")
        self._CsvWriter = csv.writer(self._File)
        if FileIsNew:
            self._CsvWriter.writerow(config.OUTPUT_COLUMNS)
            self._File.flush()

    def _LoadCompletedUrls(self):
        if not os.path.exists(self.CsvPath):
            return set()
        try:
            ExistingDataFrame = pd.read_csv(self.CsvPath)
            # Only URLs that finished successfully count as "done" — errored
            # rows are worth retrying on a resumed run.
            Completed = (
                ExistingDataFrame[ExistingDataFrame["Status/Error"] == "Success"][
                    "Website URL"
                ]
                .astype(str)
                .tolist()
            )
            return set(Completed)
        except Exception as Ex:
            logging.warning(f"Could not read existing {self.CsvPath} for resume: {Ex}")
            return set()

    def WriteResult(self, Result):
        Row = Result.to_row()
        self._CsvWriter.writerow(Row)
        self._RowsWrittenSinceFlush += 1
        if self._RowsWrittenSinceFlush >= config.WRITER_FLUSH_EVERY:
            self._File.flush()
            os.fsync(self._File.fileno())
            self._RowsWrittenSinceFlush = 0

        if self.Dashboard:
            try:
                self.Dashboard.append_row(Row)
            except Exception as Ex:
                logging.debug(
                    f"Google Sheets append failed for {Result.WebsiteUrl}: {Ex}"
                )

    def Close(self):
        self._File.flush()
        self._File.close()

    def ExportXlsx(self, XlsxPath=config.OUTPUT_XLSX_PATH):
        try:
            pd.read_csv(self.CsvPath).to_excel(XlsxPath, index=False)
            logging.info(f"Excel copy written to {XlsxPath}.")
        except Exception as Ex:
            logging.warning(f"Could not write Excel copy: {Ex}")


def SetupGoogleSheetDashboard():
    """
    Best-effort Google Sheets dashboard. Returns None (not a crash) if
    credentials aren't set up — the run continues with CSV/Excel output only.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        logging.info("Authorizing connection credentials with Google Workspace API...")
        # Client.open() looks the spreadsheet up by name via the Drive API,
        # not just Sheets — without the Drive scope too, Google rejects the
        # lookup with "insufficient authentication scopes" even though the
        # Sheets scope alone is enough for reading/writing rows.
        Scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        Creds = Credentials.from_service_account_file(
            config.CREDENTIALS_PATH, scopes=Scopes
        )
        Client = gspread.authorize(Creds)
        Sheet = Client.open(config.GOOGLE_SHEET_NAME).sheet1
        if len(Sheet.get_all_values()) == 0:
            Sheet.append_row(config.OUTPUT_COLUMNS)
        logging.info("Google Sheets dashboard connected.")
        return Sheet
    except FileNotFoundError:
        logging.warning(
            f"Cloud initialization bypassed: no such file or directory: '{config.CREDENTIALS_PATH}'"
        )
        logging.warning("Continuing execution with local CSV/Excel output only.")
        return None
    except Exception as Ex:
        logging.warning(
            f"Google Sheets setup failed ({Ex}); continuing with local output only."
        )
        return None
