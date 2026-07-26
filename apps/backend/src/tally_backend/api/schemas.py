"""Request and response models for the public API.

Kept separate from the ORM so the wire shape is a deliberate choice rather than
whatever the database happens to look like -- and so a column rename cannot
silently become a breaking API change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..db.models import Company, Connector, ConnectorStatus, Role

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    #: 10 rather than 8: this guards a company's complete financial history, and
    #: the app offers a password manager anyway.
    password: str = Field(min_length=10, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)
    org_name: str = Field(min_length=1, max_length=200)
    device_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    device_name: str | None = Field(default=None, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None
    org_id: str
    org_name: str
    role: Role


# --------------------------------------------------------------------------
# Connectors
# --------------------------------------------------------------------------


class CreateConnectorRequest(BaseModel):
    name: str = Field(default="Tally PC", max_length=200)


class ConnectorPairingResponse(BaseModel):
    """Returned exactly once, at creation.

    ``secret`` is never retrievable again -- only its hash is stored. Losing it
    means re-pairing, which is the correct trade: a secret the server can read
    back is a secret a database leak hands to an attacker.
    """

    connector_id: str
    secret: str
    name: str
    pairing_code: str


class ConnectorResponse(BaseModel):
    id: str
    name: str
    status: ConnectorStatus
    online: bool
    tally_online: bool
    last_seen_at: datetime | None
    hostname: str | None
    os: str | None
    connector_version: str | None
    companies_open: list[str] = Field(default_factory=list)
    company_count: int = 0

    @classmethod
    def build(
        cls,
        connector: Connector,
        *,
        online: bool,
        companies_open: list[str] | None = None,
        company_count: int = 0,
    ) -> ConnectorResponse:
        return cls(
            id=connector.id,
            name=connector.name,
            status=connector.status,
            online=online,
            # A connector can be connected to us while Tally itself is closed;
            # the two states drive very different messages in the app.
            tally_online=online and connector.last_tally_online,
            last_seen_at=connector.last_seen_at,
            hostname=connector.hostname,
            os=connector.os,
            connector_version=connector.connector_version,
            companies_open=companies_open or [],
            company_count=company_count,
        )


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


class LinkCompanyRequest(BaseModel):
    """Adopt a company that the connector reports as open in Tally."""

    tally_name: str = Field(min_length=1, max_length=300)
    display_name: str | None = Field(default=None, max_length=300)


class CompanyResponse(BaseModel):
    id: str
    name: str
    tally_name: str
    connector_id: str
    base_currency: str
    is_active: bool
    financial_year_from: date | None = None

    @classmethod
    def build(cls, company: Company) -> CompanyResponse:
        return cls(
            id=company.id,
            name=company.label,
            tally_name=company.tally_name,
            connector_id=company.connector_id,
            base_currency=company.base_currency,
            is_active=company.is_active,
            financial_year_from=(
                company.financial_year_from.date() if company.financial_year_from else None
            ),
        )


class DiscoveredCompany(BaseModel):
    tally_name: str
    guid: str | None = None
    linked: bool = False
    company_id: str | None = None


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

FetchModeParam = Literal["auto", "cached", "live"]


class DataEnvelope(BaseModel):
    """Every data response carries its own freshness.

    Non-negotiable in an accounting product: a number with no "as of" is a
    number the owner cannot safely act on.
    """

    data: Any
    meta: dict[str, Any]


class ReportRequest(BaseModel):
    from_date: date
    to_date: date
    mode: FetchModeParam = "auto"
