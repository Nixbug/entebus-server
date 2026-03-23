"""
Operator Image API Router for EnteBus.

Provides endpoints for managing operator images, including creation, deletion.
Uses Pydantic schemas for input validation and structured output.
Endpoints for retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Form, File, UploadFile, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.src.buckets import OPERATOR_IMAGES
from app.src.db import OperatorImage, OperatorToken, SessionLocal, ExecutiveToken
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.enums import OrderIn
from app.src.filters import IDFilter, PaginationFilter, UpdatedOnFilter, CreatedOnFilter
from app.src.permissions.operator import PermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_OPERATOR_IMAGE, URL_OPERATOR_PICTURE
from app.src.minio import delete_file, download_file, upload_file
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    get_operator_images,
    update_if_changed,
    apply_id_filters,   apply_created_on_filters,
    apply_updated_on_filters,   apply_name_filters,
    get_executive_roles,
    get_operator_roles,
    validate_image,
)
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)

route_executive = APIRouter()
route_operator = APIRouter()


## Output Schema
class OperatorImageSchema(BaseModel):
    """Schema for operator image response."""

    id: int
    company_id: int
    operator_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


## Input Forms
class CreateForm(BaseModel):
    """Form data for creating a new operator image."""

    company_id: int | None = Field(Form(default=None))
    operator_id: int | None = Field(Form(default=None))
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
    URL_OPERATOR_PICTURE,
    tags=["Operator Account Image"],
    response_model=OperatorImageSchema,
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
            **Uploads an operator image.**    
            - Operator must have a valid access token.   
            - Logged-in operator must have `operator.update` permission to upload other operator images.   
            - Operator can update their own image without permission.    
        """
    ),
)
async def upload_operator_image_executive(
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
        operator_image = OperatorImage(
            company_id=form_param.company_id,
            operator_id=form_param.operator_id,
            file_name=form_param.file.filename,
            file_type=form_param.file.content_type,
            file_size=len(file_bytes),
        )
        session.add(operator_image_image)
        session.flush()
        upload_file(
            OPERATOR_IMAGES_IMAGES,
            str(operator_image.id),
            len(file_bytes),
            BytesIO(file_bytes),
        )
        session.commit()
        session.refresh(operator_image)

        operator_image_data = jsonable_encoder(operator_image)
        log_event(token, request_info, operator_image_data)
        return operator_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()