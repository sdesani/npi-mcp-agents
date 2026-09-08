"""Pydantic v2 models for NPPES inputs and outputs.

The NPPES API returns loosely-typed JSON with many optional keys, so every
field below that is not guaranteed by the upstream contract is optional.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

US_STATE_CODE = Annotated[
    str,
    Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="Two-letter USPS state or territory code, e.g. 'MO', 'KS', 'CA'.",
    ),
]

NPI_NUMBER = Annotated[
    str,
    Field(
        min_length=10,
        max_length=10,
        pattern=r"^\d{10}$",
        description="A 10-digit National Provider Identifier, e.g. '1234567893'.",
    ),
]


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# --------------------------------------------------------------------------
# Tool inputs
# --------------------------------------------------------------------------


class ProviderSearchRequest(_Base):
    """Arguments for a provider name search."""

    first_name: str | None = Field(
        default=None,
        description="Provider's given/first name. Optional wildcard '*' suffix is supported by NPPES.",
    )
    last_name: str | None = Field(
        default=None,
        description="Provider's family/last name, or an organization name for org NPIs.",
    )
    state: US_STATE_CODE | None = Field(
        default=None,
        description="Two-letter state code to restrict the search to.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of provider records to return (NPPES caps this at 200).",
    )

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("state")
    @classmethod
    def _upper_state(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class NPILookupRequest(_Base):
    """Arguments for a single-NPI lookup."""

    npi_number: NPI_NUMBER


# --------------------------------------------------------------------------
# Tool outputs
# --------------------------------------------------------------------------


class AddressRecord(_Base):
    """A mailing or practice-location address attached to an NPI."""

    purpose: str | None = Field(
        default=None,
        alias="address_purpose",
        description="'LOCATION' for the practice address, 'MAILING' for the mailing address.",
    )
    address_1: str | None = Field(default=None, description="First street address line.")
    address_2: str | None = Field(default=None, description="Second street address line, if any.")
    city: str | None = Field(default=None)
    state: str | None = Field(default=None, description="Two-letter state code.")
    postal_code: str | None = Field(default=None, description="ZIP or ZIP+4 code.")
    country_code: str | None = Field(default=None)
    telephone_number: str | None = Field(default=None)
    fax_number: str | None = Field(default=None)


class SpecialtyRecord(_Base):
    """A single taxonomy (specialty) entry for a provider."""

    code: str | None = Field(
        default=None,
        description="NUCC Health Care Provider Taxonomy code, e.g. '207Q00000X'.",
    )
    description: str | None = Field(
        default=None,
        alias="desc",
        description="Human-readable specialty name, e.g. 'Family Medicine'.",
    )
    taxonomy_group: str | None = Field(default=None, description="NUCC taxonomy grouping, if provided.")
    primary: bool = Field(
        default=False,
        description="True when this is the provider's primary self-declared specialty.",
    )
    state: str | None = Field(
        default=None,
        description="Two-letter state in which the associated license was issued.",
    )
    license_number: str | None = Field(default=None, alias="license")


class ProviderRecord(_Base):
    """A normalized NPPES provider record."""

    npi_number: str = Field(description="The provider's 10-digit NPI.")
    enumeration_type: Literal["NPI-1", "NPI-2"] | None = Field(
        default=None,
        description="'NPI-1' for an individual provider, 'NPI-2' for an organization.",
    )
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)
    middle_name: str | None = Field(default=None)
    credential: str | None = Field(default=None, description="Credential string, e.g. 'M.D.', 'D.O.', 'NP'.")
    organization_name: str | None = Field(
        default=None,
        description="Legal business name; populated for NPI-2 organization records.",
    )
    gender: str | None = Field(default=None, description="'M' or 'F' as reported to NPPES.")
    status: str | None = Field(default=None, description="'A' when the NPI is active.")
    enumeration_date: str | None = Field(default=None, description="ISO date the NPI was issued.")
    last_updated: str | None = Field(default=None, description="ISO date the NPPES record last changed.")
    sole_proprietor: str | None = Field(default=None)
    addresses: list[AddressRecord] = Field(default_factory=list)
    specialties: list[SpecialtyRecord] = Field(
        default_factory=list,
        description="Taxonomies (specialties) declared by the provider.",
    )

    @property
    def display_name(self) -> str:
        """Best-effort human label for the record."""
        if self.organization_name:
            return self.organization_name
        parts = [p for p in (self.first_name, self.middle_name, self.last_name) if p]
        return " ".join(parts) or self.npi_number


class HealthStatus(_Base):
    """Payload returned by the /health endpoint."""

    status: Literal["ok"] = Field(description="Liveness indicator; always 'ok' when the process responds.")
    version: str = Field(description="Installed npi-mcp package version.")
    uptime_seconds: float = Field(description="Seconds since the server process started.")


# --------------------------------------------------------------------------
# Derived-intelligence outputs (not provided by NPPES itself)
# --------------------------------------------------------------------------


class NPIValidationResult(_Base):
    """Outcome of an offline NPI format and checksum validation."""

    npi_number: str = Field(description="The NPI exactly as it was supplied by the caller.")
    is_valid: bool = Field(
        description="True only when the value is 10 digits AND the check digit is correct.",
    )
    check_digit_valid: bool = Field(
        description=(
            "True when the 10th digit matches the CMS Luhn check digit. False when it does "
            "not, and also False when the value is too malformed for the check to run."
        ),
    )
    reason: str = Field(
        description=(
            "Human-readable explanation of the result. On check-digit failure this states "
            "the check digit that was expected."
        ),
    )


class ProviderStatusReport(_Base):
    """Referral-eligibility assessment derived from a live NPPES record."""

    npi_number: str = Field(description="The NPI that was assessed.")
    provider_name: str | None = Field(
        default=None,
        description="Provider or organization name, when the record could be retrieved.",
    )
    primary_specialty: str | None = Field(
        default=None,
        description="Description of the taxonomy flagged primary, when one exists.",
    )
    is_eligible_for_referral: bool = Field(
        description="True only when every eligibility check passed and `concerns` is empty.",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description=(
            "One actionable message per failed check. Empty when the provider is eligible."
        ),
    )


class SpecialtyMatchResult(_Base):
    """Providers in a state whose taxonomies match a plain-English specialty keyword."""

    specialty_keyword: str = Field(description="The keyword as supplied by the caller.")
    matched_taxonomy_codes: list[str] = Field(
        default_factory=list,
        description="NUCC taxonomy codes the keyword resolved to. Empty if unrecognized.",
    )
    state: str | None = Field(default=None, description="Two-letter state the search was scoped to.")
    providers: list[ProviderRecord] = Field(
        default_factory=list,
        description="Matching providers. Empty when nothing matched or the keyword is unknown.",
    )
    result_count: int = Field(default=0, description="Number of providers returned.")
    message: str = Field(
        description=(
            "Human-readable outcome. When the keyword is unrecognized this lists every "
            "supported keyword so the caller can retry without guessing."
        ),
    )
