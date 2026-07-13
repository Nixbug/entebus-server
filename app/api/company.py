"""
Company API router.

Provides endpoints for managing companies:
    - POST (executive)
    - PATCH (executive, operator)
    - GET (executive, operator, public)
    - DELETE (executive)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Union
from fastapi import APIRouter, Query, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from shapely import wkt
from shapely.geometry import Point
from sqlalchemy.orm.session import Session
from sqlalchemy import func, String, or_
from geoalchemy2 import Geography

from app.api.bearer import oauth2_executive, bearer_operator
from app.src import schemas
from app.src.buckets import OPERATOR_IMAGES, VEHICLE_IMAGES
from app.src.db import (
    Company,
    CompanyWallet,
    ExecutiveToken,
    OperatorToken,
    VehicleImage,
    Wallet,
    OperatorImage,
    OperatorRole,
    get_db_session,
)
from app.src.permissions.operator import PermissionSchema as PermissionSchemaOP
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.enums import CompanyStatus, CompanyType, OrderIn
from app.src.urls import URL_COMPANY
from app.src.schemas import PatchForm
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    validate_id,
    verify_token,
    validate_srid_4326,
    validate_wkt_string,
    authorize_executive,
    authorize_operator,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_request_info,
    load_geometry,
    to_WKB,
    update_if_changed,
    resolve_model_defaults,
    apply_status_filters,
    apply_type_filters,
)

route_executive = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class MaskedCompanySchema(BaseModel):
    """Schema for company response without revealing all details."""

    id: int
    name: str
    type: int


class CompanySchema(MaskedCompanySchema):
    """Schema for company response."""

    status: int
    description: str | None
    address: str
    location: str
    updated_on: datetime | None
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
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


class UpdateFormForOP(PatchForm):
    """Form for updating a company by operator."""

    description: Annotated[str | None, "nullable"] = Field(
        default=None, min_length=1, max_length=1024
    )
    address: str | None = Field(default=None, min_length=1, max_length=512)
    location: str | None = Field(
        default=None,
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        ),
    )


class UpdateFormForEX(UpdateFormForOP):
    """Form for updating a company by executive."""

    name: str | None = Field(
        min_length=1, max_length=32, pattern=NAME_PATTERN, default=None
    )
    status: CompanyStatus | None = Field(
        description=enum_str(CompanyStatus), default=None
    )
    type: CompanyType | None = Field(description=enum_str(CompanyType), default=None)


class UpdateForm(UpdateFormForEX):
    """Form for updating a company by executive or operator."""

    pass


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering company results."""

    ID = "id"
    CREATED_ON = "created_on"
    UPDATED_ON = "updated_on"
    LOCATION = "location"


class QueryParamsForPU(
    IDFilter, CreatedOnFilter, NameFilter, PaginationFilter, UpdatedOnFilter
):
    """Query parameters for public users."""

    search: str | None = Field(Query(default=None))
    location: str | None = Field(
        Query(
            default=None,
            description=(
                f"Accepts only SRID 4326 (WGS84), valid WKT string representing a `POINT`. Used for distance-based ordering."
            ),
        )
    )
    type_list: list[CompanyType] | None = Field(
        Query(default=None, description=enum_str(CompanyType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForPU):
    """Query parameters for executives."""

    status_list: list[CompanyStatus] | None = Field(
        Query(default=None, description=enum_str(CompanyStatus))
    )
    address: str | None = Field(Query(default=None))
    description: str | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# ---------------------------------------------------------------------------
## Helper Functions
# ---------------------------------------------------------------------------
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


def company_to_dict(company: Company) -> dict:
    """
    Convert a Company SQLAlchemy model instance to a dictionary with WKT location in WKT format.

    Args:
        company (Company): Company model instance.

    Returns:
        dict: Dictionary representation of the company with company location in WKT format.
    """
    company_data = jsonable_encoder(
        company,
        exclude={Company.location.name},
    )
    company_data[Company.location.name] = (load_geometry(company.location)).wkt
    return company_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_company(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new Company with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new company.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created company data with location in WKT format.
    """
    # Validate location (WKT and SRID)
    location_geom = validate_location(form_param.location)
    company = Company(
        name=form_param.name,
        status=form_param.status,
        type=form_param.type,
        description=form_param.description,
        address=form_param.address,
        location=to_WKB(location_geom),
    )
    session.add(company)

    # Create default operator roles for the company (Admin and Guest)
    admin_permissions = PermissionSchemaOP.all_granted().model_dump()
    guest_permissions = PermissionSchemaOP.all_denied().model_dump()
    admin_role = OperatorRole(
        company_id=company.id, name="Admin", permissions=admin_permissions
    )
    guest_role = OperatorRole(
        company_id=company.id, name="Guest", permissions=guest_permissions
    )
    session.add_all([admin_role, guest_role])

    # Create Wallet
    wallet = Wallet(name=form_param.name, balance=0)
    session.add(wallet)
    session.flush()

    # Link Company to Wallet
    company_wallet = CompanyWallet(company_id=company.id, wallet_id=wallet.id)
    session.add(company_wallet)
    session.commit()
    session.refresh(company)

    company_data = company_to_dict(company)
    log_event(token, request_info, company_data)
    return company_data


def update_company(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, OperatorToken],
    request_info: schemas.RequestInfo,
    company_filter=None,
) -> dict:
    """
    Updates a Company with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the company to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (Union[ExecutiveToken, OperatorToken]): Authenticated executive or operator token.
        request_info (schemas.RequestInfo): Request information for logging.
        company_filter (Optional): Additional filter for company validation.

    Returns:
        dict: JSON-encoded representation of the updated company.
    """
    company = validate_id(session, Company, id, Company.id, extra_filter=company_filter)

    update_data = form_param.model_dump(exclude_unset=True)
    wallet = None
    if "location" in update_data:
        old_location_geom = load_geometry(company.location)
        new_location_geom = validate_location(update_data["location"])
        if not new_location_geom.equals(old_location_geom):
            company.location = to_WKB(new_location_geom)
        update_data.pop("location")
    if "name" in update_data:
        if update_data["name"] != company.name:
            company.name = update_data["name"]
            company_wallet = (
                session.query(CompanyWallet)
                .filter(CompanyWallet.company_id == company.id)
                .first()
            )
            assert (
                company_wallet is not None
            ), "CompanyWallet should exist for the company"
            wallet = (
                session.query(Wallet)
                .filter(Wallet.id == company_wallet.wallet_id)
                .first()
            )
            assert wallet is not None, "Wallet should exist for the company"
            wallet.name = update_data["name"]
        update_data.pop("name")

    update_if_changed(company, update_data)
    if session.is_modified(company) or (
        wallet is not None and session.is_modified(wallet)
    ):
        session.commit()
        session.refresh(company)
        company_data = company_to_dict(company)
        log_event(token, request_info, company_data)
    else:
        company_data = company_to_dict(company)
    return company_data


def delete_company(
    session: Session, id: int, token: ExecutiveToken, request_info: schemas.RequestInfo
) -> None:
    """
    Delete a company from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the company to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    company = session.query(Company).filter(Company.id == id).first()
    if company is None:
        return

    operator_images = (
        session.query(OperatorImage).filter(OperatorImage.company_id == id).all()
    )
    vehicle_images = (
        session.query(VehicleImage).filter(VehicleImage.company_id == id).all()
    )
    company_data = company_to_dict(company)
    session.delete(company)
    session.commit()

    # Delete operator images from object storage
    for operator_image in operator_images:
        delete_file(OPERATOR_IMAGES, str(operator_image.id))
    # Delete vehicle images from object storage
    for vehicle_image in vehicle_images:
        delete_file(VEHICLE_IMAGES, str(vehicle_image.id))

    log_event(token, request_info, company_data)


def search_companies(session: Session, query_params: QueryParams) -> list[Company]:
    """
    Search for companies based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve companies that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Company]: List of companies that match the search criteria.
    """
    query = session.query(Company)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.address is not None:
        query = query.filter(Company.address.ilike(f"%{query_params.address}%"))
    if query_params.description is not None:
        query = query.filter(Company.description.ilike(f"%{query_params.description}%"))

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Company.id.cast(String).ilike(search),
                Company.name.ilike(search),
                Company.address.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Company, query_params)
    query = apply_created_on_filters(query, Company, query_params)
    query = apply_updated_on_filters(query, Company, query_params)
    query = apply_name_filters(query, Company, query_params)
    query = apply_status_filters(query, Company, query_params)
    query = apply_type_filters(query, Company, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                Company.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = Company.id
    else:
        ordering_attr = getattr(Company, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    companies = query.all()
    return companies


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
]

PATCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.UnknownValue(Company.id),
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidWKTStringOrType(),
    exceptions.InvalidSRID4326(),
    exceptions.InvalidToken(),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Creates a new company.")
    .add_line("Duplicate names are not allowed.")
    .add_line("By default the company is created in under verification status.")
    .add_line("By default the company type is other.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing company.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("When updating location, it must be a valid SRID 4326 WKT POINT.")
    .add_line(
        "If the company name is updated, the linked wallet name will also be updated to maintain consistency."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes a company.")
    .add_line("Returns 204 No Content even if the specified company does not exist.")
    .add_line(
        "Deleting a company will delete all related records (operators, tokens, roles, images, wallets). Use with caution."
    )
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of companies.")
    .add_line("Common search supports searching by id, name and address.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_COMPANY,
    summary="Create company",
    tags=["Company"],
    response_model=CompanySchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.create` permission.")
        .to_string()
    ),
)
async def create_company_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_COMPANY],
        )
        return create_company(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_COMPANY}/{{id}}",
    summary="Update company",
    tags=["Company"],
    response_model=CompanySchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.update` permission.")
        .to_string()
    ),
)
async def update_company_for_executive(
    id: int,
    form_param: UpdateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY],
        )
        return update_company(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_COMPANY,
    summary="Fetch company",
    tags=["Company"],
    response_model=list[CompanySchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_companies_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            company_to_dict(company)
            for company in search_companies(
                session, QueryParams(**query_params.model_dump())
            )
        ]
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_COMPANY}/{{id}}",
    summary="Delete company",
    tags=["Company"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("The logged-in executive must have `company.delete` permission.")
        .to_string()
    ),
)
async def delete_company_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_COMPANY],
        )
        delete_company(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.patch(
    f"{URL_COMPANY}/{{id}}",
    summary="Update company",
    tags=["Company"],
    response_model=CompanySchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.update` permission.")
        .to_string()
    ),
)
async def update_company_for_operator(
    id: int,
    form_param: UpdateFormForOP,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY],
        )
        return update_company(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            company_filter=(Company.id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_COMPANY,
    summary="Fetch company",
    tags=["Company"],
    response_model=list[CompanySchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_companies_for_operator(
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        query_params = resolve_model_defaults(
            QueryParams, id=token.company_id, offset=0, limit=1
        )
        return [
            company_to_dict(company)
            for company in search_companies(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_COMPANY,
    summary="Fetch company",
    tags=["Company"],
    response_model=list[MaskedCompanySchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        GET_DESCRIPTION.copy()
        .add_line("Only verified companies are returned.")
        .add_line("Only masked data is returned.")
        .to_string()
    ),
)
async def fetch_companies_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        query_params = QueryParams(
            **query_params.model_dump(),
            status_list=[CompanyStatus.VERIFIED],
            address=None,
            description=None,
        )
        return search_companies(session, query_params)
    except Exception as e:
        exceptions.handle(e)
