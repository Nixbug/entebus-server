"""
Company API Router for EnteBus.

Provides endpoints for managing companies, including creation.
Uses Pydantic schemas for input validation and structured output.
Endpoints for update, deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from shapely import wkb, wkt
from shapely.geometry import Point

from app.api.bearer import oauth2_executive
from app.src.db import Company, ExecutiveToken, SessionLocal, Wallet, CompanyWallet
from app.src.permissions.executive import PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.enums import CompanyStatus, CompanyType
from app.src.urls import URL_COMPANY
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_request_info,
    validate_srid_4326,
    validate_wkt_string,
)

route_executive = APIRouter()


## Output Schema
class CompanySchema(BaseModel):
    """Schema for company response."""

    id: int
    name: str
    status: int
    type: int
    address: str
    location: str
    updated_on: datetime | None
    created_on: datetime


## Input Forms
class CreateForm(BaseModel):
    """Form for creating a company."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    status: CompanyStatus = Field(
        description=enum_str(CompanyStatus), default=CompanyStatus.UNDER_VERIFICATION
    )
    type: CompanyType = Field(
        description=enum_str(CompanyType), default=CompanyType.OTHER
    )
    description: str | None = Field(default=None, max_length=1024)
    address: str = Field(max_length=512)
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_COMPANY,
    tags=["Company"],
    response_model=CompanySchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Creates a new company.**  
            - Executive must have a valid access token.     
            - Logged-in executive must have `company.create` permission.       
            - Duplicate name are not allowed.   
            - By default the company is created in `under verification` status.   
            - By default the company type is `other`.     
        """
    ),
)
async def create_company(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, PermissionPath.CREATE_COMPANY)

        # Validate WKT and SRID
        location_geom = validate_wkt_string(form_param.location, Point)
        validate_srid_4326(location_geom)
        validated_location = wkt.dumps(location_geom)

        company = Company(
            name=form_param.name,
            status=form_param.status,
            type=form_param.type,
            description=form_param.description,
            address=form_param.address,
            location=validated_location,
        )
        session.add(company)
        session.commit()
        session.refresh(company)

        # Create Wallet
        walletName = form_param.name + "wallet"
        wallet = Wallet(name=walletName, balance=0)
        session.add(wallet)
        session.flush()

        # Link Company to Wallet
        company_wallet = CompanyWallet(company_id=company.id, wallet_id=wallet.id)
        session.add(company_wallet)
        session.commit()
        session.refresh(company)

        company_data = jsonable_encoder(company, exclude={Company.location.name})
        company_data[Company.location.name] = (
            wkb.loads(bytes(company.location.data))
        ).wkt
        log_event(token, request_info, company_data)
        return company_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
