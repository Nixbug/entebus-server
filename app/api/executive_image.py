"""
Executive Image API Router for EnteBus.

Provides endpoints for managing executive images, including creation,
deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from enum import StrEnum
from typing import List
from fastapi import APIRouter, Depends, Response, Query, status, Form, UploadFile, File
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from io import BytesIO
from datetime import datetime
from sqlalchemy.orm.session import Session

from app.src.buckets import EXECUTIVE_IMAGES
from app.src import exceptions
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, PictureFilter
from app.src.urls import URL_EXECUTIVE_PICTURE
from app.src.minio import delete_file, download_file, upload_file
from app.api.bearer import oauth2_executive
from app.src.db import Executive, ExecutiveToken, ExecutiveImage, SessionLocal
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
async def create_executive_image(session: Session, form_param: CreateForm) -> dict:
    """
    Creates a new ExecutiveImage with the provided form data.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new executive image.

    Returns:
        dict: Created executive image data.
    """
    validate_id(session, Executive, form_param.executive_id, ExecutiveImage.executive_id)
    file_bytes = await form_param.file.read()
    validate_image(file_bytes, form_param.file.filename)
    executive_image = ExecutiveImage(
        executive_id=form_param.executive_id,
        file_name=form_param.file.filename,
        file_type=form_param.file.content_type,
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
    return jsonable_encoder(executive_image)


def search_executive_images(
    session: Session, query_params: QueryParams
) -> List[ExecutiveImage]:
    """
    Searches for executive images based on the provided query parameters.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters for filtering and pagination.

    Returns:
        List[ExecutiveImage]: List of executive images matching the search criteria.
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


def search_executive_image(
    session: Session, id: int, query_params: ImageQueryParams
) -> StreamingResponse:
    """
    Retrieves an executive image by its ID and optionally resizes it.

    Args:
        session (Session): SQLAlchemy database session.
        id (int): ID of the executive image to retrieve.
        query_params (ImageQueryParams): Query parameters for resizing the image.

    Returns:
        StreamingResponse: The executive image if found, else raises an exception.
    """
    executive_image = (
        session.query(ExecutiveImage).filter(ExecutiveImage.id == id).first()
    )
    if executive_image is not None:
        file_bytes = download_file(EXECUTIVE_IMAGES, str(executive_image.id))
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
    raise exceptions.UnknownValue(ExecutiveImage.id)


def delete_executive_image(
    session: Session, executive_image: ExecutiveImage | None
) -> tuple[bool, dict]:
    """
    Deletes an executive image.

    Args:
        session (Session): SQLAlchemy database session.
        executive_image (ExecutiveImage | None): The executive image to delete or None.

    Returns:
        Tuple[bool, dict]:
            - bool: True if the executive image was found and deleted, False otherwise.
            - dict: JSON-encoded representation of the deleted executive image.
    """
    if executive_image is not None:
        executive_image_data = jsonable_encoder(executive_image)
        session.delete(executive_image)
        session.commit()

        delete_file(EXECUTIVE_IMAGES, str(executive_image.id))
        return True, executive_image_data
    return False, {}


# ---------------------------------------------------------------------------
## Common exceptions
# ---------------------------------------------------------------------------
POST_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
    exceptions.InvalidImageFile(),
    exceptions.UnknownValue(ExecutiveImage.executive_id),
]

DELETE_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.NoPermission(),
]

GET_EXCEPTIONS = [
    exceptions.InvalidToken(),
]

DOWNLOAD_EXCEPTIONS = [
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
    .add_line("Returns 204 No Content even if the specified image does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches executive images.")

DOWNLOAD_DESCRIPTION = Description().add_head(
    "Downloads executive profile picture in original or resized resolution."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
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
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)

        if form_param.executive_id is None:
            form_param.executive_id = token.executive_id
        is_self_update = form_param.executive_id == token.executive_id
        if not is_self_update:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)

        executive_image_data = await create_executive_image(session, form_param)
        log_event(token, request_info, executive_image_data)
        return executive_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)

        executive_image = (
            session.query(ExecutiveImage).filter(ExecutiveImage.id == id).first()
        )
        if (
            executive_image is None
            or executive_image.executive_id != token.executive_id
        ):
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)

        deleted, executive_image_data = delete_executive_image(session, executive_image)
        if deleted:
            log_event(token, request_info, executive_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


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
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_executive_images(session, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    f"{URL_EXECUTIVE_PICTURE}/{{id}}",
    summary="Download executive image",
    tags=["Account Image"],
    responses=fuse_exception_responses(DOWNLOAD_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_executive_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_executive_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
