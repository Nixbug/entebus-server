"""
Executive Image API router.

Provides endpoints for managing executive images:
    - POST (executive)
    - DELETE (executive)
    - GET (executive)
    - GET /{id} (executive)
"""

from datetime import datetime
from enum import StrEnum
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.src.buckets import EXECUTIVE_IMAGES
from app.src import exceptions, schemas
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, PictureFilter
from app.src.urls import URL_EXECUTIVE_PICTURE
from app.src.minio import delete_file, download_file, upload_file
from app.api.bearer import oauth2_executive
from app.src.db import Executive, ExecutiveImage, ExecutiveToken, get_db_session
from app.src.permissions.executive import PermissionPath
from app.src.openobserve import log_event
from app.src.description import Description
from app.src.validators import (
    verify_permission,
    verify_token,
    validate_id,
    validate_image,
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
    get_executive_roles,
    resize_image,
)

route_executive = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class ExecutiveImageSchema(BaseModel):
    """Schema for executive image response."""

    id: int
    executive_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class CreateForm(BaseModel):
    """Form data for creating a new executive image."""

    executive_id: int | None = Field(Form(default=None))
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


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParams(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for executive image endpoints."""

    executive_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class ImageQueryParams(BaseModel):
    """Query parameters for retrieving an executive image."""

    width: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )
    height: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
async def create_executive_image(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
    executive_id: int,
) -> dict:
    """
    Create a new executive image in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive image.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
        executive_id (int): ID of the executive for whom the image is being created.

    Returns:
        dict: Created executive image data.
    """
    validate_id(session, Executive, executive_id, ExecutiveImage.executive_id)

    file_bytes = await form_param.file.read()
    filename = form_param.file.filename
    if not filename:
        raise exceptions.InvalidValue("filename")
    validate_image(file_bytes, filename)

    content_type = form_param.file.content_type
    if not content_type:
        raise exceptions.InvalidValue("content_type")

    executive_image = ExecutiveImage(
        executive_id=executive_id,
        file_name=filename,
        file_type=content_type,
        file_size=len(file_bytes),
    )
    session.add(executive_image)
    session.flush()
    upload_file(
        EXECUTIVE_IMAGES,
        str(executive_image.id),
        len(file_bytes),
        BytesIO(file_bytes),
    )
    session.commit()
    session.refresh(executive_image)

    executive_image_data = jsonable_encoder(executive_image)
    log_event(token, request_info, executive_image_data)
    return executive_image_data


def delete_executive_image(
    session: Session,
    executive_image: ExecutiveImage,
    token: ExecutiveToken,
    request_info: schemas.RequestInfo,
):
    """
    Delete an executive image from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        executive_image (ExecutiveImage): Executive image instance to delete.
        token (ExecutiveToken): Authenticated executive token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    executive_image_data = jsonable_encoder(executive_image)
    session.delete(executive_image)
    session.commit()
    delete_file(EXECUTIVE_IMAGES, str(executive_image.id))
    log_event(token, request_info, executive_image_data)


def search_executive_images(
    session: Session, query_params: QueryParams
) -> list[ExecutiveImage]:
    """
    Search for executive images based on provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve executive images that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[ExecutiveImage]: List of executive images that match the search criteria.
    """
    query = session.query(ExecutiveImage)
    if query_params.executive_id is not None:
        query = query.filter(ExecutiveImage.executive_id == query_params.executive_id)

    # Generalized filters
    query = apply_id_filters(query, ExecutiveImage, query_params)
    query = apply_created_on_filters(query, ExecutiveImage, query_params)
    query = apply_picture_filters(query, ExecutiveImage, query_params)

    # Ordering and pagination
    ordering_attr = getattr(ExecutiveImage, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    executive_images = query.all()
    return executive_images


def fetch_executive_image(
    session: Session, id: int, query_params: ImageQueryParams
) -> StreamingResponse:
    """
    Fetch an executive image by its ID and optionally resize it.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the executive image to fetch.
        query_params (ImageQueryParams): Query parameters for resizing the image.

    Returns:
        StreamingResponse: The executive image stream in original or resized form.
    """
    executive_image = get_by_id(session, ExecutiveImage, id)
    if executive_image is None:
        raise exceptions.UnknownValue(ExecutiveImage.id)

    file_bytes = download_file(EXECUTIVE_IMAGES, str(executive_image.id))
    assert file_bytes is not None, "Downloaded file bytes should not be None"
    resized_bytes = resize_image(
        file_bytes,
        width=query_params.width,
        height=query_params.height,
    )
    return StreamingResponse(
        BytesIO(resized_bytes),
        media_type=executive_image.file_type,
        headers={
            "Content-Disposition": f'inline; filename="{executive_image.file_name}"',
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
    exceptions.UnknownValue(ExecutiveImage.executive_id),
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
    exceptions.UnknownValue(ExecutiveImage.id),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = (
    Description()
    .add_head("Uploads an executive image.")
    .add_line("Executives can upload their own image without additional permissions.")
    .add_line(
        "To upload another executive's image, the `executive.update` permission is required."
    )
)

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an executive image.")
    .add_line("Executives can delete their own image without additional permissions.")
    .add_line(
        "To delete another executive's image, the `executive.update` permission is required."
    )
    .add_line(
        "Even with permission if the image does not exist, the operation returns 204 No Content."
    )
)

GET_DESCRIPTION = Description().add_head("Fetches executive images.")

DOWNLOAD_DESCRIPTION = Description().add_head(
    "Downloads executive profile picture in original or resized resolution."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_PICTURE,
    summary="Create executive image",
    tags=["Account Image"],
    response_model=ExecutiveImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(POST_DESCRIPTION.to_string()),
)
async def upload_executive_image_for_executive(
    form_param: CreateForm = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, ExecutiveToken, access_token)
        executive_id = form_param.executive_id or token.executive_id
        if executive_id != token.executive_id:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)

        return await create_executive_image(
            session, form_param, token, request_info, executive_id
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_EXECUTIVE_PICTURE}/{{id}}",
    summary="Delete executive image",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(DELETE_DESCRIPTION.to_string()),
)
async def delete_executive_image_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, ExecutiveToken, access_token)
        executive_image = get_by_id(session, ExecutiveImage, id)
        if executive_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if executive_image.executive_id != token.executive_id:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)

        delete_executive_image(session, executive_image, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_EXECUTIVE_PICTURE,
    summary="Fetch executive image",
    tags=["Account Image"],
    response_model=list[ExecutiveImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_executive_images_for_executive(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_executive_images(session, query_params)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    f"{URL_EXECUTIVE_PICTURE}/{{id}}",
    summary="Download executive image",
    tags=["Account Image"],
    responses=fuse_exception_responses(FETCH_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_executive_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return fetch_executive_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)
