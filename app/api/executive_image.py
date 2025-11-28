"""
Executive Image API Router for EnteBus.

Provides endpoints for managing executive images, including creation,
deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from enum import StrEnum
from fastapi import APIRouter, Depends, Query, status, Form, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from io import BytesIO
from datetime import datetime
from sqlalchemy import String, or_

from app.src.buckets import EXECUTIVE_IMAGES
from app.src import exceptions
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, PictureFilter
from app.src.urls import URL_EXECUTIVE_PICTURE
from app.src.minio import download_file, upload_file
from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveToken, ExecutiveImage, SessionLocal
from app.src.permissions.executive import PermissionPath
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
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
    orm_to_json,
    resize_image,
    validate_image,
)

route_executive = APIRouter()


## Output Schema
class ExecutiveImageSchema(BaseModel):
    """Schema for executive image response."""

    id: int
    executive_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


## Input Forms
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


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParams(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for executive image endpoints."""

    executive_id: int | None = Field(Query(default=None))
    search: str | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class ImageQueryParams(BaseModel):
    """Query parameters for retrieving an executive image."""

    width: int | None = Field(Query(default=None, ge=16, le=2048))
    height: int | None = Field(Query(default=None, ge=16, le=2048))


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_PICTURE,
    tags=["Account Image"],
    response_model=ExecutiveImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidImageFile(),
        ]
    ),
    description=(
        """
            **Uploads an executive image.**    
            - Executive must have a valid access token.   
            - Logged-in executive must have `executive.update` permission to upload other executive images.   
            - Executive can update their own image without permission.    
        """
    ),
)
async def upload_executive_image(
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

        _, executive_image_data = orm_to_json(executive_image)
        log_event(token, request_info, executive_image_data)
        return executive_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_EXECUTIVE_PICTURE,
    tags=["Account Image"],
    response_model=list[ExecutiveImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches executive images.**    
            - Requires a valid access token for authentication.    
            - Common search supports searching by id, executive_id, file_name, file_type, and file_size.    
        """
    ),
)
async def fetch_executive_image(
    query_params: QueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        query = session.query(ExecutiveImage)

        if query_params.executive_id is not None:
            query = query.filter(
                ExecutiveImage.executive_id == query_params.executive_id
            )
        # Common search
        if query_params.search:
            search = f"%{query_params.search}%"
            query = query.filter(
                or_(
                    ExecutiveImage.file_name.ilike(search),
                    ExecutiveImage.file_type.ilike(search),
                    ExecutiveImage.file_size.cast(String).ilike(search),
                    ExecutiveImage.id.cast(String).ilike(search),
                    ExecutiveImage.executive_id.cast(String).ilike(search),
                )
            )
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
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    f"{URL_EXECUTIVE_PICTURE}/{{id}}",
    tags=["Account Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(ExecutiveImage.id)]
    ),
)
async def download_executive_image(
    id: int,
    qParam: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    """
    **Download executive profile picture in original or resized resolution.**

    - Requires a valid access token for authentication.
    """
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        executive_image = (
            session.query(ExecutiveImage).filter(ExecutiveImage.id == id).first()
        )
        if executive_image is not None:
            file_bytes = download_file(EXECUTIVE_IMAGES, str(executive_image.id))
            resized_bytes = resize_image(
                file_bytes,
                width=qParam.width,
                height=qParam.height,
            )

            return StreamingResponse(
                BytesIO(resized_bytes),
                media_type=executive_image.file_type,
                headers={
                    "Content-Disposition": f"file_name={executive_image.file_name}",
                    "Cache-Control": "public, max-age=31536000, immutable",
                },
            )
        raise exceptions.UnknownValue(ExecutiveImage.id)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
