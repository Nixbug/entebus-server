"""
Company API Router for EnteBus.

Provides endpoints for managing companies, including creation, update,
Uses Pydantic schemas for input validation and structured output.
Endpoints for deletion, and retrieval are planned for future implementation.
"""

from datetime import datetime
from typing import Tuple
from fastapi import APIRouter, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from shapely import wkb, wkt
from shapely.geometry import Point
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Wallet,
    CompanyWallet,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.enums import CompanyStatus, CompanyType
from app.src.urls import URL_COMPANY
from app.src.openobserve import log_event
from app.src.validators import validate_company_id, verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    validate_srid_4326,
    validate_wkt_string,
)

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class CompanySchema(BaseModel):
    """Schema for company response."""

    id: int
    name: str
    status: int
    type: int
    description: str | None
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
    description: str | None = Field(default=None, min_length=1, max_length=1024)
    address: str = Field(min_length=1, max_length=512)
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


class UpdateFormForOP(BaseModel):
    """Form for updating a company by operator."""

    description: str | None = Field(default=None, min_length=1, max_length=1024)
    address: str = Field(default=None, min_length=1, max_length=512)
    location: str = Field(
        default=None,
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        ),
    )


class UpdateFormForEX(UpdateFormForOP):
    """Form for updating a company by executive."""

    name: str = Field(
        min_length=1, max_length=32, pattern=NAME_PATTERN, default=None
    )
    status: CompanyStatus = Field(
        description=enum_str(CompanyStatus),
        default=None,
    )
    type: CompanyType = Field(
        description=enum_str(CompanyType),
        default=None,
    )


class UpdateForm(UpdateFormForEX):
    """Form for updating a company by executive or operator."""

    pass


# Function
def validate_location(location_wkt: str) -> Point:
    """
    Validate a WKT string as a Point geometry with SRID 4326.

    Args:
        location_wkt (str): Location in WKT format (Point geometry).

    Returns:
        Point: Validated Shapely `Point` geometry.
    """
    # Validate WKT and SRID
    location_geom = validate_wkt_string(location_wkt, Point)
    validate_srid_4326(location_geom)

    return location_geom


def update_company(
    session: Session, company: Company, form_param: UpdateForm
) -> Tuple[bool, dict]:

    update_data = form_param.model_dump(exclude_unset=True)
    print("Update data:", update_data)
    # Validate location if changed
    if form_param.location is not None:
        new_geom = validate_location(form_param.location)
        old_geom = wkb.loads(bytes(company.location.data))

        if new_geom.wkt != old_geom.wkt:
            company.location = wkt.dumps(new_geom)
        update_data.pop("location")

    wallet = None
    if form_param.name is not None:
        if form_param.name != company.name:
            company.name = form_param.name
            company_wallet = (
                session.query(CompanyWallet)
                .filter(CompanyWallet.company_id == company.id)
                .first()
            )
            wallet = (
                session.query(Wallet)
                .filter(Wallet.id == company_wallet.wallet_id)
                .first()
            )
            wallet.name = form_param.name
        update_data.pop("name")

    update_if_changed(company, update_data)
    have_updates = session.is_modified(company) or (
        wallet and session.is_modified(wallet)
    )
    if have_updates:
        session.commit()
        session.refresh(company)

    company_data = jsonable_encoder(
        company,
        exclude={Company.location.name},
    )
    company_data[Company.location.name] = (wkb.loads(bytes(company.location.data))).wkt
    return have_updates, company_data


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_COMPANY,
    tags=["Company"],
    response_model=CompanySchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
        ]
    ),
    description=(
        """
            **Creates a new company.**  
            - Executive must have a valid access token.     
            - Logged-in executive must have `company.create` permission.       
            - Duplicate names are not allowed.   
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
        verify_permission(roles, ExecutivePermissionPath.CREATE_COMPANY)

        # Validate location (WKT and SRID)
        location_geom = validate_location(form_param.location)
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

        # Create Wallet
        wallet = Wallet(name=form_param.name, balance=0)
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


@route_executive.patch(
    f"{URL_COMPANY}/{{id}}",
    tags=["Company"],
    response_model=CompanySchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Company.id),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
        ]
    ),
    description=(
        """
            **Updates an existing company.**    
            - Requires a valid access token.    
            - Logged-in executive must have `company.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - When updating `location`, it must be a valid SRID 4326 WKT POINT.    
            - If the company name is updated, the linked wallet name will also be updated to maintain consistency.    
        """
    ),
)
async def update_company_executive(
    id: int,
    form_param: UpdateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY)

        company = validate_company_id(session, id)
        have_updates, company_data = update_company(
            session, company, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, company_data)
        return company_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_COMPANY}/{{id}}",
    tags=["Company"],
    response_model=CompanySchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Company.id),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
        ]
    ),
    description=(
        """
            **Updates an existing company.**
            - Requires a valid access token.
            - Logged-in operator must have `company.update` permission.
            - Empty PATCH requests are allowed and will result in no changes.
            - When updating `location`, it must be a valid SRID 4326 WKT POINT.
        """
    ),
)
async def update_company_operator(
    id: int,
    form_param: UpdateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY)

        company = validate_company_id(session, id)
        if token.company_id != company.id:
            raise exceptions.NoPermission()
        have_updates, company_data = update_company(
            session, company, UpdateForm(**form_param.model_dump(exclude_unset=True))
        )

        if have_updates:
            log_event(token, request_info, company_data)
        return company_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
