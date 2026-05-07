"""
Business API Router for EnteBus.

Provides endpoints for managing businesses, including creation,
update, deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from enum import StrEnum
from typing import Tuple, List
from fastapi import APIRouter, Query, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from shapely import wkb, wkt
from shapely.geometry import Point
from sqlalchemy.orm.session import Session
from sqlalchemy import func, String, or_
from geoalchemy2 import Geography

from app.api.bearer import oauth2_executive, bearer_vendor
from app.src.buckets import VENDOR_IMAGES
from app.src.db import (
    Business,
    BusinessWallet,
    ExecutiveToken,
    VendorImage,
    VendorToken,
    SessionLocal,
    Wallet,
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
from app.src.openobserve import log_event
from app.src.validators import validate_id, verify_permission, verify_token, validate_srid_4326, validate_wkt_string
from app.src.functions import (
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_vendor_roles,
    get_request_info,
    update_if_changed,
    
    resolve_model_defaults,
    apply_status_filters,
    apply_type_filters,
)

route_executive = APIRouter()
route_vendor = APIRouter()
route_public = APIRouter()


## Output Schema
class MaskedBusinessSchema(BaseModel):
    """Schema for business response for public users without revealing all details."""

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


## Input Forms
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


class UpdateFormForVE(BaseModel):
    """Form for updating a business by vendor."""

    description: str | None = Field(default=None, min_length=1, max_length=1024)
    address: str = Field(default=None, min_length=1, max_length=512)
    location: str = Field(
        default=None,
        description=(
            f"Accepts only SRID 4326 (WGS84), "
            f"valid WKT string representing a `POINT`."
        ),
    )


class UpdateFormForEX(UpdateFormForVE):
    """Form for updating a business by executive."""

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN, default=None)
    status: BusinessStatus = Field(
        description=enum_str(BusinessStatus),
        default=None,
    )
    type: BusinessType = Field(
        description=enum_str(BusinessType),
        default=None,
    )


class UpdateForm(UpdateFormForEX):
    """Form for updating a business by executive or vendor."""

    pass


## Query Parameters
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
    type_list: List[BusinessType] | None = Field(
        Query(default=None, description=enum_str(BusinessType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForPU):
    """Query parameters for executives."""

    status_list: List[BusinessStatus] | None = Field(
        Query(default=None, description=enum_str(BusinessStatus))
    )
    address: str | None = Field(Query(default=None, min_length=1, max_length=512))
    description: str | None = Field(Query(default=None, min_length=1, max_length=1024))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


# Functions
def validate_location(location_wkt: str) -> Point:
    """
    Validate a WKT string as a Point geometry with SRID 4326.

    Args:
        location_wkt (str): Location in WKT format (Point geometry).

    Returns:
        Point: Validated Shapely `Point` geometry.
    """
    location_geom = validate_wkt_string(location_wkt, Point)
    validate_srid_4326(location_geom)
    return location_geom


def update_business(
    session: Session, business: Business, form_param: UpdateForm
) -> Tuple[bool, dict]:
    """
    Updates a Business with the provided form data.

    Args:
        session (Session): SQLAlchemy database session.
        business (Business): Business to update.
        form_param (UpdateForm): Form data containing fields to update.

    Returns:
        Tuple[bool, dict]:
            - bool: True if the business was modified and the changes were committed.
            - dict: JSON-encoded representation of the updated business.
    """
    update_data = form_param.model_dump(exclude_unset=True)
    # Validate location if changed
    if form_param.location is not None:
        new_geom = validate_location(form_param.location)
        old_geom = wkb.loads(bytes(business.location.data))

        if new_geom.wkt != old_geom.wkt:
            business.location = wkt.dumps(new_geom)
        update_data.pop("location")

    wallet = None
    if form_param.name is not None:
        if form_param.name != business.name:
            business.name = form_param.name
            business_wallet = (
                session.query(BusinessWallet)
                .filter(BusinessWallet.business_id == business.id)
                .first()
            )
            wallet = (
                session.query(Wallet)
                .filter(Wallet.id == business_wallet.wallet_id)
                .first()
            )
            wallet.name = form_param.name
        update_data.pop("name")

    update_if_changed(business, update_data)
    have_updates = session.is_modified(business) or (
        wallet and session.is_modified(wallet)
    )
    if have_updates:
        session.commit()
        session.refresh(business)

    business_data = jsonable_encoder(
        business,
        exclude={Business.location.name},
    )
    business_data[Business.location.name] = (
        wkb.loads(bytes(business.location.data))
    ).wkt
    return have_updates, business_data


def search_business(session: Session, query_params: QueryParams) -> List[Business]:
    """
    Search for businesses based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve businesses that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Business]: List of businesses that match the search criteria.
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

    query = query.with_entities(
        Business,
        func.ST_AsText(Business.location).label("location_wkt"),
    )
    results = query.all()
    businesses = []
    for business_obj, location_wkt in results:
        business_obj.location = location_wkt
        businesses.append(business_obj)

    return businesses


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_BUSINESS,
    tags=["Business"],
    response_model=BusinessSchema,
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
            **Creates a new business.**  
            - Executive must have a valid access token.     
            - Logged-in executive must have `business.create` permission.       
            - Duplicate names are not allowed.   
            - By default the business is created in `active` status.   
            - By default the business type is `other`.     
        """
    ),
)
async def create_business(
    form_param: CreateForm,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.CREATE_BUSINESS)

        # Validate location (WKT and SRID)
        location_geom = validate_location(form_param.location)
        validated_location = wkt.dumps(location_geom)

        business = Business(
            name=form_param.name,
            status=form_param.status,
            type=form_param.type,
            description=form_param.description,
            address=form_param.address,
            location=validated_location,
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

        business_data = jsonable_encoder(business, exclude={Business.location.name})
        business_data[Business.location.name] = (
            wkb.loads(bytes(business.location.data))
        ).wkt
        log_event(token, request_info, business_data)
        return business_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.patch(
    f"{URL_BUSINESS}/{{id}}",
    tags=["Business"],
    response_model=BusinessSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Business.id),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
        ]
    ),
    description=(
        """
            **Updates an existing business.**    
            - Requires a valid access token.    
            - Logged-in executive must have `business.update` permission.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - When updating `location`, it must be a valid SRID 4326 WKT POINT.    
            - If the business name is updated, the linked wallet name will also be updated to maintain consistency.    
        """
    ),
)
async def update_business_executive(
    id: int,
    form_param: UpdateFormForEX,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_BUSINESS)

        business = validate_id(session, Business, id, Business.id)
        have_updates, business_data = update_business(
            session,
            business,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
        )

        if have_updates:
            log_event(token, request_info, business_data)
        return business_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_BUSINESS,
    tags=["Business"],
    response_model=List[BusinessSchema],
    responses=fuse_exception_responses(
        [
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
            exceptions.InvalidToken(),
        ]
    ),
    description=(
        """
            **Fetches a list of businesses.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, name and address.    
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_business_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_business(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_BUSINESS}/{{id}}",
    tags=["Business"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a business.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have `business.delete` permission.    
            - Returns 204 No Content even if the specified business does not exist.    
            - Deleting a business will delete all related records (vendors, tokens, roles, images, wallets). Use with caution.    
        """
    ),
)
async def delete_business_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_BUSINESS)

        business = session.query(Business).filter(Business.id == id).first()
        if business is not None:
            vendor_images = (
                session.query(VendorImage).filter(VendorImage.business_id == id).all()
            )
            business_data = jsonable_encoder(
                business,
                exclude={Business.location.name},
            )
            business_data[Business.location.name] = (
                wkb.loads(bytes(business.location.data))
            ).wkt
            session.delete(business)
            session.commit()

            # Delete vendor images from object storage
            for vendor_image in vendor_images:
                delete_file(VENDOR_IMAGES, str(vendor_image.id))

            log_event(token, request_info, business_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.patch(
    f"{URL_BUSINESS}/{{id}}",
    tags=["Business"],
    response_model=BusinessSchema,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.UnknownValue(Business.id),
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
        ]
    ),
    description=(
        """
            **Updates an existing business.**   
            - Requires a valid access token.    
            - Logged-in vendor must have `business.update` permission.    
            - Vendor can only update the business they belong to.    
            - Empty PATCH requests are allowed and will result in no changes.    
            - When updating `location`, it must be a valid SRID 4326 WKT POINT.    
            - Returns the updated business.    
        """
    ),
)
async def update_business_vendor(
    id: int,
    form_param: UpdateFormForVE,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)
        roles = get_vendor_roles(session, token)
        verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS)

        business = validate_id(session, Business, id, Business.id)
        if token.business_id != business.id:
            raise exceptions.NoPermission()
        have_updates, business_data = update_business(
            session,
            business,
            UpdateForm(**form_param.model_dump(exclude_unset=True)),
        )

        if have_updates:
            log_event(token, request_info, business_data)
        return business_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    URL_BUSINESS,
    tags=["Business"],
    response_model=List[BusinessSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches the vendor's business.**    
            - Only the business associated with the vendor will be returned.    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_business_vendor(access_token=Depends(bearer_vendor)):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        query_params = resolve_model_defaults(
            QueryParams, id_list=[token.business_id], offset=0, limit=1
        )
        return search_business(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_BUSINESS,
    tags=["Business"],
    response_model=List[MaskedBusinessSchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of businesses.**    
            - Only active businesses are returned.    
            - Only id, name, type are returned.    
            - Common search supports searching by id, name and address.    
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.       
        """
    ),
)
async def fetch_business_public(
    query_params: QueryParamsForPU = Depends(),
):
    try:
        session = SessionLocal()

        return search_business(
            session,
            QueryParams(
                **query_params.model_dump(),
                status_list=[BusinessStatus.ACTIVE],
                address=None,
                description=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
