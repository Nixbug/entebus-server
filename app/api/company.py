"""
Company API Router for EnteBus.

Provides endpoints for managing companies, including creation,
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

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.buckets import OPERATOR_IMAGES
from app.src.db import (
    Company,
    ExecutiveToken,
    OperatorToken,
    SessionLocal,
    Wallet,
    CompanyWallet,
    OperatorImage,
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
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.enums import CompanyStatus, CompanyType, OrderIn
from app.src.urls import URL_COMPANY
from app.src.openobserve import log_event
from app.src.validators import validate_company_id, verify_permission, verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
    apply_id_filters,
    enum_str,
    fuse_exception_responses,
    get_executive_roles,
    get_operator_roles,
    get_request_info,
    update_if_changed,
    validate_srid_4326,
    validate_wkt_string,
    resolve_model_defaults,
    apply_status_filters,
    apply_type_filters,
)

route_executive = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


## Output Schema
class MaskedCompanySchema(BaseModel):
    """Schema for company response for public users without revealing all details."""

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

    name: str = Field(min_length=1, max_length=32, pattern=NAME_PATTERN, default=None)
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


## Query Parameters
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
    type_list: List[CompanyType] | None = Field(
        Query(default=None, description=enum_str(CompanyType))
    )
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForPU):
    """Query parameters for executives."""

    status_list: List[CompanyStatus] | None = Field(
        Query(default=None, description=enum_str(CompanyStatus))
    )
    address: str | None = Field(Query(default=None, min_length=1, max_length=512))
    description: str | None = Field(Query(default=None, min_length=1, max_length=1024))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


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


def search_company(session: Session, query_params: QueryParams) -> List[Company]:
    """
    Search for companies based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve companies that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[Company]: List of companies that match the search criteria.
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

    query = query.with_entities(
        Company,
        func.ST_AsText(Company.location).label("location_wkt"),
    )
    results = query.all()
    companies = []
    for company_obj, location_wkt in results:
        company_obj.location = location_wkt
        companies.append(company_obj)

    return companies


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


@route_executive.get(
    URL_COMPANY,
    tags=["Company"],
    response_model=List[CompanySchema],
    responses=fuse_exception_responses(
        [
            exceptions.InvalidWKTStringOrType(),
            exceptions.InvalidSRID4326(),
            exceptions.InvalidToken(),
        ]
    ),
    description=(
        """
            **Fetches a list of companies.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, name and address.    
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_company_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_company(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_COMPANY}/{{id}}",
    tags=["Company"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a company.**    
            - Requires a valid access token for authentication.    
            - The logged-in executive must have `company.delete` permission.    
            - Returns 204 No Content even if the specified company does not exist.    
            - Deleting a company will delete all related records (operators, tokens, roles, images, wallets). Use with caution.    
        """
    ),
)
async def delete_company_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.DELETE_COMPANY)

        company = session.query(Company).filter(Company.id == id).first()
        if company is not None:
            operator_images = (
                session.query(OperatorImage)
                .filter(OperatorImage.company_id == id)
                .all()
            )
            company_data = jsonable_encoder(
                company,
                exclude={Company.location.name},
            )
            company_data[Company.location.name] = (
                wkb.loads(bytes(company.location.data))
            ).wkt
            session.delete(company)
            session.commit()

            # Delete operator images
            for operator_image in operator_images:
                delete_file(OPERATOR_IMAGES, str(operator_image.id))

            log_event(token, request_info, company_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
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


@route_operator.get(
    URL_COMPANY,
    tags=["Company"],
    response_model=List[CompanySchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches the operator's company.**    
            - Only the company associated with the operator will be returned.    
        """
    ),
)
async def fetch_company_operator(access_token=Depends(bearer_operator)):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        query_params = resolve_model_defaults(
            QueryParams, id_list=[token.company_id], offset=0, limit=1
        )
        return search_company(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_COMPANY,
    tags=["Company"],
    response_model=List[MaskedCompanySchema],
    responses=fuse_exception_responses(
        [exceptions.InvalidWKTStringOrType(), exceptions.InvalidSRID4326()]
    ),
    description=(
        """
            **Fetches a list of companies.**    
            - Only verified companies are returned.
            - Only id, name, type are returned.
            - Common search supports searching by id, name and address.    
            - If `location` is not provided while using `order_by=location`, the API will fall back to default ordering by `id`.    
        """
    ),
)
async def fetch_company_public(
    query_params: QueryParamsForPU = Depends(),
):
    try:
        session = SessionLocal()

        return search_company(
            session,
            QueryParams(
                **query_params.model_dump(),
                status_list=[CompanyStatus.VERIFIED],
                address=None,
                description=None,
            ),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
