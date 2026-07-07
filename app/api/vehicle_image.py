"""
Vehicle Image API router.

Provides endpoints for managing vehicle images:
    - POST (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator, public)
    - GET /{id} (executive, operator, public)
"""

from datetime import datetime
from enum import StrEnum
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import oauth2_executive, bearer_operator
from app.src.buckets import VEHICLE_IMAGES
from app.src import exceptions, schemas
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, PictureFilter
from app.src.urls import URL_VEHICLE_PICTURE
from app.src.minio import delete_file, download_file, upload_file
from app.src.db import (
    ExecutiveToken,
    OperatorToken,
    Vehicle,
    VehicleImage,
    get_db_session,
)
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    verify_token,
    validate_id,
    validate_image,
    authorize_executive,
    authorize_operator,
)
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_picture_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_request_info,
    resize_image,
)

route_executive = APIRouter()
route_operator = APIRouter()
route_public = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class VehicleImageSchema(BaseModel):
    """Schema for vehicle image response."""

    id: int
    company_id: int
    vehicle_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class ImageUploadForm(BaseModel):
    """Form data for uploading a vehicle image."""

    file: UploadFile = Field(
        File(
            description=(
                f"Max File Size: {MAX_IMAGE_FILE_SIZE // (1024*1024)} MB, "
                f"Min File Size: {MIN_IMAGE_FILE_SIZE // 1024} KB, "
                f"Max Resolution: {MAX_IMAGE_RESOLUTION} x {MAX_IMAGE_RESOLUTION} px, "
                f"Min Resolution: {MIN_IMAGE_RESOLUTION} x {MIN_IMAGE_RESOLUTION} px"
            )
        )
    )


class CreateFormForOP(ImageUploadForm):
    """Form data for creating a new vehicle image for an operator."""

    vehicle_id: int = Field(Form())


class CreateFormForEX(CreateFormForOP):
    """Form data for creating a new vehicle image for an executive."""

    pass


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vehicle image."""

    pass


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParamsForPU(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for public."""

    vehicle_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForOP(QueryParamsForPU):
    """Query parameters for operators."""

    pass


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


class ImageQueryParams(BaseModel):
    """Query parameters for retrieving a vehicle image."""

    width: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )
    height: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
async def create_vehicle_image(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    vehicle_filter=None,
) -> dict:
    """
    Creates a new vehicle image record in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a vehicle image.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vehicle_filter (optional): Additional filter to apply when validating the vehicle.

    Returns:
        dict: The created vehicle image data.
    """
    vehicle = validate_id(
        session,
        Vehicle,
        form_param.vehicle_id,
        VehicleImage.vehicle_id,
        extra_filter=vehicle_filter,
    )

    file_bytes = await form_param.file.read()
    filename = form_param.file.filename
    if not filename:
        raise exceptions.InvalidValue("filename")
    validate_image(file_bytes, filename)

    content_type = form_param.file.content_type
    if not content_type:
        raise exceptions.InvalidValue("content_type")

    vehicle_image = VehicleImage(
        company_id=vehicle.company_id,
        vehicle_id=vehicle.id,
        file_name=filename,
        file_type=content_type,
        file_size=len(file_bytes),
    )
    session.add(vehicle_image)
    session.flush()
    upload_file(
        VEHICLE_IMAGES,
        str(vehicle_image.id),
        len(file_bytes),
        BytesIO(file_bytes),
    )
    session.commit()
    session.refresh(vehicle_image)

    vehicle_image_data = jsonable_encoder(vehicle_image)
    log_event(token, request_info, vehicle_image_data)
    return vehicle_image_data


def delete_vehicle_image(
    session: Session,
    id: int,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    vehicle_filter=None,
):
    """
    Deletes a vehicle image from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vehicle image to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vehicle_filter (optional): Additional filter to apply when validating the vehicle.
    """
    vehicle_image = get_by_id(session, VehicleImage, id, extra_filter=vehicle_filter)
    if vehicle_image is None:
        raise exceptions.UnknownValue(VehicleImage.id)

    vehicle_image_data = jsonable_encoder(vehicle_image)
    session.delete(vehicle_image)
    session.commit()
    delete_file(VEHICLE_IMAGES, str(vehicle_image.id))
    log_event(token, request_info, vehicle_image_data)


def search_vehicle_images(
    session: Session, query_params: QueryParams
) -> list[VehicleImage]:
    """
    Search for vehicle images based on provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve vehicle images that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[VehicleImage]: List of vehicle images that match the search criteria.
    """
    query = session.query(VehicleImage)
    if query_params.company_id is not None:
        query = query.filter(VehicleImage.company_id == query_params.company_id)
    if query_params.vehicle_id is not None:
        query = query.filter(VehicleImage.vehicle_id == query_params.vehicle_id)

    # Generalized filters
    query = apply_id_filters(query, VehicleImage, query_params)
    query = apply_created_on_filters(query, VehicleImage, query_params)
    query = apply_picture_filters(query, VehicleImage, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VehicleImage, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vehicle_images = query.all()
    return vehicle_images


def fetch_vehicle_image(
    session: Session,
    id: int,
    query_params: ImageQueryParams,
    vehicle_filter=None,
) -> StreamingResponse:
    """
    Fetch a vehicle image by its ID and optionally resize it.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the vehicle image to fetch.
        query_params (ImageQueryParams): Query parameters for resizing the image.
        vehicle_filter (optional): Additional filter to apply when validating the vehicle.

    Returns:
        StreamingResponse: The vehicle image stream in original or resized form.
    """
    vehicle_image = get_by_id(session, VehicleImage, id, extra_filter=vehicle_filter)
    if vehicle_image is None:
        raise exceptions.UnknownValue(VehicleImage.id)

    file_bytes = download_file(VEHICLE_IMAGES, str(vehicle_image.id))
    assert file_bytes is not None, "Downloaded file bytes should not be None"
    resized_bytes = resize_image(
        file_bytes,
        width=query_params.width,
        height=query_params.height,
    )
    return StreamingResponse(
        BytesIO(resized_bytes),
        media_type=vehicle_image.file_type,
        headers={
            "Content-Disposition": f'inline; filename="{vehicle_image.file_name}"',
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidImageFile(),
    exceptions.UnknownValue(VehicleImage.vehicle_id),
    exceptions.InvalidValue("filename"),
    exceptions.InvalidValue("content_type"),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]

FETCH_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.UnknownValue(VehicleImage.id),
]


# ---------------------------------------------------------------------------
## Common descriptions
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Uploads a vehicle image.")
    .add_line("A valid access token is required for authentication.")
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes a vehicle image.")
    .add_line("Returns 204 No Content even if the specified image does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vehicle images.")

DOWNLOAD_DESCRIPTION = Description().add_head(
    "Download vehicle image in original or resized resolution."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VEHICLE_PICTURE,
    summary="Create vehicle image",
    tags=["Vehicle Image"],
    response_model=VehicleImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.vehicle.update` permission.")
        .to_string()
    ),
)
async def upload_vehicle_image_for_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE],
        )
        return create_vehicle_image(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    summary="Delete vehicle image",
    tags=["Vehicle Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.vehicle.update` permission.")
        .to_string()
    ),
)
async def delete_vehicle_image_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE],
        )
        delete_vehicle_image(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_VEHICLE_PICTURE,
    summary="Fetch vehicle image",
    tags=["Vehicle Image"],
    response_model=list[VehicleImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicle_images_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_vehicle_images(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    summary="Download vehicle image",
    tags=["Vehicle Image"],
    responses=fuse_exception_responses(FETCH_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_vehicle_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return fetch_vehicle_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_VEHICLE_PICTURE,
    summary="Create vehicle image",
    tags=["Vehicle Image"],
    response_model=VehicleImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.vehicle.update` permission.")
        .to_string()
    ),
)
async def upload_vehicle_image_for_operator(
    form_param: CreateFormForOP = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_VEHICLE],
        )
        return create_vehicle_image(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            vehicle_filter=(Vehicle.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    summary="Delete vehicle image",
    tags=["Vehicle Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in operator must have `company.vehicle.update` permission.")
        .to_string()
    ),
)
async def delete_vehicle_image_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_operator(
            session,
            access_token.credentials,
            [OperatorPermissionPath.UPDATE_COMPANY_VEHICLE],
        )
        delete_vehicle_image(session, id, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_VEHICLE_PICTURE,
    summary="Fetch vehicle image",
    tags=["Vehicle Image"],
    response_model=list[VehicleImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicle_images_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_vehicle_images(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    summary="Download vehicle image",
    tags=["Vehicle Image"],
    responses=fuse_exception_responses(FETCH_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_vehicle_image_for_operator(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return fetch_vehicle_image(
            session,
            id,
            query_params,
            vehicle_filter=(Vehicle.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Public]
# ---------------------------------------------------------------------------
@route_public.get(
    URL_VEHICLE_PICTURE,
    summary="Fetch vehicle image",
    tags=["Vehicle Image"],
    response_model=list[VehicleImageSchema],
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vehicle_images_for_public(
    query_params: QueryParamsForPU = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return search_vehicle_images(
            session,
            QueryParams(**query_params.model_dump(), company_id=None),
        )
    except Exception as e:
        exceptions.handle(e)


@route_public.get(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    summary="Download vehicle image",
    tags=["Vehicle Image"],
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_vehicle_image_for_public(
    id: int,
    query_params: ImageQueryParams = Depends(),
    session: Session = Depends(get_db_session),
):
    try:
        return fetch_vehicle_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)
