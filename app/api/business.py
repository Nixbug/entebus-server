"""
Business API router.

Provides endpoint for managing businesses:
    - POST (executive)
    - PATCH (executive, vendor)
    - GET (executive, vendor, public)
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

from app.api.bearer import oauth2_executive, bearer_vendor
from app.src import schemas
from app.src.buckets import VENDOR_IMAGES
from app.src.db import (
    Business,
    BusinessWallet,
    ExecutiveToken,
    VendorToken,
    Wallet,
    VendorImage,
    get_db_session,
)
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    NameFilter,
    PaginationFilter,
    UpdatedOnFilter,
)
from app.src.minio import delete_file
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.enums import BusinessStatus, BusinessType, OrderIn
from app.src.urls import URL_BUSINESS
from app.src.schemas import PatchForm
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    validate_id,
    verify_token,
    validate_srid_4326,
    validate_wkt_string,
    authorize_executive,
    authorize_vendor,
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
route_vendor = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class MaskedBusinessSchema(BaseModel):
    """Schema for business response without revealing all details."""

    id: int
    name: str
    type: int


class BusinessSchema(MaskedBusinessSchema):
    """Schema for business response."""

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
    """Form for creating a business."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN)
    status: BusinessStatus = Field(
        description=enum_str(BusinessStatus), default=BusinessStatus.ACTIVE
    )
    type: BusinessType = Field(
        description=enum_str(BusinessType), default=BusinessType.OTHER
    )
    description: str | None = Field(default=None, min_length=1, max_length=1024)
    address: str = Field(min_length=1, max_length=512)
    location: str = Field(
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        )
    )


class UpdateFormForVE(PatchForm):
    """Form for updating a business by vendor."""

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


class UpdateFormForEX(UpdateFormForVE):
    """Form for updating a business by executive."""

    name: str | None = Field(
        min_length=1, max_length=32, pattern=NAME_PATTERN, default=None
    )
    status: BusinessStatus | None = Field(
        description=enum_str(BusinessStatus), default=None
    )
    type: BusinessType | None = Field(description=enum_str(BusinessType), default=None)


class UpdateForm(UpdateFormForEX):
    """Form for updating a business by executive or vendor."""

    pass


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering business results."""

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
    type_list: list[BusinessType] | None = Field(
        Query(default=None, description=enum_str(BusinessType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForPU):
    """Query parameters for executives."""

    status_list: list[BusinessStatus] | None = Field(
        Query(default=None, description=enum_str(BusinessStatus))
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


def business_to_dict(business: Business) -> dict:
    """
    Convert a Business SQLAlchemy model instance to a dictionary with WKT location in WKT format.

    Args:
        business (Business): Business model instance.

    Returns:
        dict: Dictionary representation of the business with business location in WKT format.
    """
    business_data = jsonable_encoder(
        business,
        exclude={Business.location.name},
    )
    business_data[Business.location.name] = (load_geometry(business.location)).wkt
    return business_data


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
def create_business(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
) -> dict:
    """
    Creates a new Business with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new business.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.

    Returns:
        dict: Created business data with location in WKT format.
    """
    # Validate location (WKT and SRID)
    location_geom = validate_location(form_param.location)
    location = wkt.dumps(location_geom)

    business = Business(
        name=form_param.name,
        status=form_param.status,
        type=form_param.type,
        description=form_param.description,
        address=form_param.address,
        location=location,
    )
    session.add(business)

    # Create Wallet
    wallet = Wallet(name=form_param.name, balance=0)
    session.add(wallet)
    session.flush()

    # Link Business to Wallet
    business_wallet = BusinessWallet(business_id=business.id, wallet_id=wallet.id)
    session.add(business_wallet)
    session.commit()
    session.refresh(business)

    business_data = business_to_dict(business)
    log_event(token, request_info, business_data)
    return business_data


def update_business(
    session: Session,
    id: int,
    form_param: UpdateForm,
    token: Union[ExecutiveToken, VendorToken],
    request_info: schemas.RequestInfo,
    business_filter=None,
) -> dict:
    """
    Updates a Business with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the business to update.
        form_param (UpdateForm): Form data containing fields to update.
        token (Union[ExecutiveToken, VendorToken]): Authenticated executive or vendor token.
        request_info (schemas.RequestInfo): Request information for logging.
        business_filter (Optional): Additional filter for business validation.

    Returns:
        dict: JSON-encoded representation of the updated business.
    """
    business = validate_id(
        session, Business, id, Business.id, extra_filter=business_filter
    )

    update_data = form_param.model_dump(exclude_unset=True)
    wallet = None
    if "location" in update_data:
        old_location_geom = load_geometry(business.location)
        new_location_geom = validate_location(update_data["location"])
        if not new_location_geom.equals(old_location_geom):
            business.location = to_WKB(new_location_geom)
        update_data.pop("location")
    if "name" in update_data:
        if update_data["name"] != business.name:
            business.name = update_data["name"]
            business_wallet = (
                session.query(BusinessWallet)
                .filter(BusinessWallet.business_id == business.id)
                .first()
            )
            assert (
                business_wallet is not None
            ), "BusinessWallet should exist for the business"
            wallet = (
                session.query(Wallet)
                .filter(Wallet.id == business_wallet.wallet_id)
                .first()
            )
            assert wallet is not None, "Wallet should exist for the business"
            wallet.name = update_data["name"]
        update_data.pop("name")

    update_if_changed(business, update_data)
    if session.is_modified(business) or (
        wallet is not None and session.is_modified(wallet)
    ):
        session.commit()
        session.refresh(business)
        business_data = business_to_dict(business)
        log_event(token, request_info, business_data)
    else:
        business_data = business_to_dict(business)
    return business_data


def search_businesses(session: Session, query_params: QueryParams) -> list[Business]:
    """
    Search for businesses based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve businesses that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[Business]: List of businesses that match the search criteria.
    """
    query = session.query(Business)
    validated_location = None
    if query_params.location is not None:
        geometry = validate_wkt_string(query_params.location, Point)
        validate_srid_4326(geometry)
        validated_location = wkt.dumps(geometry)
    if query_params.address is not None:
        query = query.filter(Business.address.ilike(f"%{query_params.address}%"))
    if query_params.description is not None:
        query = query.filter(
            Business.description.ilike(f"%{query_params.description}%")
        )

    # Common search
    if query_params.search:
        search = f"%{query_params.search}%"
        query = query.filter(
            or_(
                Business.id.cast(String).ilike(search),
                Business.name.ilike(search),
                Business.address.ilike(search),
            )
        )

    # Generalized filters
    query = apply_id_filters(query, Business, query_params)
    query = apply_created_on_filters(query, Business, query_params)
    query = apply_updated_on_filters(query, Business, query_params)
    query = apply_name_filters(query, Business, query_params)
    query = apply_status_filters(query, Business, query_params)
    query = apply_type_filters(query, Business, query_params)

    # Ordering and pagination
    if query_params.order_by == OrderBy.LOCATION:
        if validated_location is not None:
            ordering_attr = func.ST_Distance(
                Business.location.cast(Geography),
                func.ST_GeogFromText(validated_location),
            )
        else:
            ordering_attr = Business.id
    else:
        ordering_attr = getattr(Business, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    businesses = query.all()
    return businesses


def delete_business(
    session: Session, id: int, token: ExecutiveToken, request_info: schemas.RequestInfo
) -> None:
    """
    Delete a business from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the business to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    business = session.query(Business).filter(Business.id == id).first()
    if business is None:
        return

    vendor_images = (
        session.query(VendorImage).filter(VendorImage.business_id == id).all()
    )
    business_data = business_to_dict(business)
    session.delete(business)
    session.commit()

    # Delete vendor images from object storage
    for vendor_image in vendor_images:
        delete_file(VENDOR_IMAGES, str(vendor_image.id))

    log_event(token, request_info, business_data)


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
    exceptions.UnknownValue(Business.id),
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
    .add_head("Creates a new business.")
    .add_line("Duplicate names are not allowed.")
    .add_line("By default the business is created in active status.")
    .add_line("By default the business type is other.")
)

PATCH_DESCRIPTION = (
    Description()
    .add_head("Updates an existing business.")
    .add_line("Empty PATCH requests are allowed and will result in no changes.")
    .add_line("When updating location, it must be a valid SRID 4326 WKT POINT.")
    .add_line(
        "If the business name is updated, the linked wallet name will also be updated to maintain consistency."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes a business.")
    .add_line("Returns 204 No Content even if the specified business does not exist.")
    .add_line(
        "Deleting a business will delete all related records (vendors, tokens, roles, images, wallets). Use with caution."
    )
)

GET_DESCRIPTION = (
    Description()
    .add_head("Fetches a list of businesses.")
    .add_line(
        "If location is not provided while using order_by=location, the API will fall back to default ordering by id."
    )
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_BUSINESS,
    summary="Create business",
    tags=["Business"],
    response_model=BusinessSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.create` permission.")
        .to_string()
    ),
)
async def create_business_for_executive(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.CREATE_BUSINESS],
        )
        return create_business(session, form_param, token, request_info)
    except Exception as e:
        exceptions.handle(e)


@route_executive.patch(
    f"{URL_BUSINESS}/{{id}}",
    summary="Update business",
    tags=["Business"],
    response_model=BusinessSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.update` permission.")
        .to_string()
    ),
)
async def update_business_for_executive(
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
            [ExecutivePermissionPath.UPDATE_BUSINESS],
        )
        return update_business(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_BUSINESS,
    summary="Fetch business",
    tags=["Business"],
    response_model=list[BusinessSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_businesses_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return [
            business_to_dict(business)
            for business in search_businesses(
                session, QueryParams(**query_params.model_dump())
            )
        ]
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_BUSINESS}/{{id}}",
    summary="Delete business",
    tags=["Business"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("The logged-in executive must have `business.delete` permission.")
        .to_string()
    ),
)
async def delete_business_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.DELETE_BUSINESS],
        )
        delete_business(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.patch(
    f"{URL_BUSINESS}/{{id}}",
    summary="Update business",
    tags=["Business"],
    response_model=BusinessSchema,
    responses=fuse_exception_responses(PATCH_EXCEPTIONS),
    description=(
        PATCH_DESCRIPTION.copy()
        .add_line("Logged-in vendor must have `business.update` permission.")
        .to_string()
    ),
)
async def update_business_for_vendor(
    id: int,
    form_param: UpdateFormForVE,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_vendor(
            session,
            access_token.credentials,
            [VendorPermissionPath.UPDATE_BUSINESS],
        )
        return update_business(
            session,
            id,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
            token,
            request_info,
            business_filter=(Business.id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_BUSINESS,
    summary="Fetch business",
    tags=["Business"],
    response_model=list[BusinessSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_businesses_for_vendor(
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        query_params = resolve_model_defaults(
            QueryParams, id=token.business_id, offset=0, limit=1
        )
        return [
            business_to_dict(business)
            for business in search_businesses(session, query_params)
        ]
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_BUSINESS,
    summary="Fetch business",
    tags=["Business"],
    response_model=list[MaskedBusinessSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        GET_DESCRIPTION.copy()
        .add_line("Only active businesses are returned.")
        .add_line("Only masked data is returned.")
        .to_string()
    ),
)
async def fetch_businesses_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        query_params = QueryParams(
            **query_params.model_dump(),
            status_list=[BusinessStatus.ACTIVE],
            address=None,
            description=None,
        )
        return search_businesses(session, query_params)
    except Exception as e:
        exceptions.handle(e)
