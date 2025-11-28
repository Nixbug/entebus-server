"""
Executive Image API Router for EnteBus.

Provides endpoints for managing executive images, including creation,
deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from fastapi import APIRouter, Depends, Response, status, Form, UploadFile, File
from pydantic import BaseModel, Field
from io import BytesIO
from datetime import datetime

from app.src.buckets import EXECUTIVE_IMAGES
from app.src import exceptions
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)
from app.src.urls import URL_EXECUTIVE_PICTURE
from app.src.minio import delete_file, upload_file
from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveToken, ExecutiveImage, SessionLocal
from app.src.permissions.executive import PermissionPath
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    orm_to_json,
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


@route_executive.delete(
    f"{URL_EXECUTIVE_PICTURE}/{{id}}",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an executive image.**    
            - Executive must have a valid access token.    
            - Executives can delete their own image without additional permissions.    
            - To delete another executive's image, the 'executive.update' permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_executive_image(
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
        if executive_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if executive_image.executive_id != token.executive_id:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.UPDATE_EXECUTIVE)

        session.delete(executive_image)
        session.commit()
        delete_file(EXECUTIVE_IMAGES, str(executive_image.id))

        _, executive_image_data = orm_to_json(executive_image)
        log_event(token, request_info, executive_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
