"""models.py — typed result shapes shared across the scanner modules."""

from dataclasses import dataclass, field, asdict
from typing import List

import files.config as config


@dataclass
class CmpEvidence:
    """What AnalyzePageEvidence/IdentifyCmp found on a single page."""

    Url: str = ""
    Title: str = ""
    BannerPresent: str = "No"  # "Yes" / "No"
    CmpDetected: str = "N/A"  # vendor name or "Unknown" / "N/A"
    Reason: str = "None"
    FoundIds: List[str] = field(default_factory=list)
    FoundGlobals: List[str] = field(default_factory=list)


@dataclass
class PolicyEvidence:
    """What InspectPrivacyPolicy found about the site's privacy policy page."""

    PrivacyPolicyUrl: str = "Not Found"
    PrivacyPolicyHost: str = "N/A"
    ThirdPartyHosted: str = "N/A"
    DetectedPolicyOwner: str = "Unknown"
    OwnerMatchesSite: str = "N/A"


@dataclass
class ScanResult:
    """One row of the final report — column order matches config.OUTPUT_COLUMNS."""

    WebsiteUrl: str
    WebsiteName: str
    CookieBannerAvailable: str = "Unknown"
    ConsentTool: str = "Unknown"
    PrivacyPolicyUrl: str = "Not Found"
    PrivacyPolicyHost: str = "N/A"
    ThirdPartyHostedPolicy: str = "N/A"
    DetectedPolicyOwner: str = "Unknown"
    OwnerMatchesSite: str = "N/A"
    StatusError: str = "Error"

    def to_row(self):
        """Return values in config.OUTPUT_COLUMNS order, for CSV/Sheets writes."""
        Mapping = {
            "Website URL": self.WebsiteUrl,
            "Website Name": self.WebsiteName,
            "Cookie Banner Available": self.CookieBannerAvailable,
            "Consent Tool": self.ConsentTool,
            "Privacy Policy URL": self.PrivacyPolicyUrl,
            "Privacy Policy Host": self.PrivacyPolicyHost,
            "Third-Party Hosted Policy": self.ThirdPartyHostedPolicy,
            "Detected Policy Owner": self.DetectedPolicyOwner,
            "Owner Matches Site": self.OwnerMatchesSite,
            "Status/Error": self.StatusError,
        }
        return [Mapping[Col] for Col in config.OUTPUT_COLUMNS]

    def to_dict(self):
        Row = self.to_row()
        return dict(zip(config.OUTPUT_COLUMNS, Row))
