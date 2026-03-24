"""
Operator Image API Router for EnteBus.

Provides endpoints for managing operator images, including creation, deletion.
Uses Pydantic schemas for input validation and structured output.
Endpoints for retrieval are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Form, File, UploadFile, Response, status, Depends
from fastapi.encoders import jsonable_encoder
from io import BytesIO
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api import executive_image
from app.src.buckets import OPERATOR_IMAGES
from app.src.db import OperatorImage, OperatorToken, SessionLocal, ExecutiveToken
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.enums import OrderIn
from app.src.filters import IDFilter, PaginationFilter, UpdatedOnFilter, CreatedOnFilter
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src import exceptions
from app.src.regex import NAME_PATTERN
from app.src.urls import URL_OPERATOR_PICTURE
from app.src.minio import delete_file, download_file, upload_file
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    enum_str,
    fuse_exception_responses,
    get_request_info,
    update_if_changed,
    apply_id_filters,
    apply_created_on_filters,
    apply_updated_on_filters,
    apply_name_filters,
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
class ImageUploadForm(BaseModel):
    """Form data for uploading an operator image."""

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


class CreateFormForEx(ImageUploadForm):
    """Form data for creating a new operator image for an operator."""

    company_id: int = Field(Form())
    operator_id: int = Field(Form())


class CreateFormForOP(ImageUploadForm):
    """Form data for creating a new operator image for an operator."""

    operator_id: int | None = Field(Form(default=None))


# Functions
def create_operator_image(
    session: Session, operator_image: OperatorImage, file_bytes: bytes
) -> OperatorImage:
    """
    Creates a new operator image record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        operator_image (OperatorImage): Operator image data to create.
        file_bytes (bytes): The image file bytes.

    Returns:
        OperatorImage: The created operator image record.
    """
    session.add(operator_image)
    session.flush()
    upload_file(
        OPERATOR_IMAGES,
        str(operator_image.id),
        len(file_bytes),
        BytesIO(file_bytes),
    )
    session.commit()
    session.refresh(operator_image)
    operator_image_data = jsonable_encoder(operator_image)
    return operator_image_data


def delete_operator_image(session: Session, operator_image: OperatorImage) -> dict:
    """
    Deletes an operator image and its associated file from storage.

    Args:
        session (Session): SQLAlchemy database session.
        operator_image (OperatorImage): Operator image to delete.

    Returns:
        dict: deleted operator image data for logging purposes.
    """
    operator_image_data = jsonable_encoder(operator_image)
    session.delete(operator_image)
    session.commit()
    delete_file(OPERATOR_IMAGES, str(operator_image.id))
    return operator_image_data


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
    form_param: CreateFormForEx = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR)

        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)
        operator_image = OperatorImage(
            company_id=form_param.company_id,
            operator_id=form_param.operator_id,
            file_name=form_param.file.filename,
            file_type=form_param.file.content_type,
            file_size=len(file_bytes),
        )
        operator_image_data = create_operator_image(session, operator_image, file_bytes)
        log_event(token, request_info, operator_image_data)
        return operator_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    tags=["Operator Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an operator image.**    
            - Operator must have a valid access token.    
            - Operators can delete their own image without additional permissions.    
            - To delete another operator's image, the `company.operator.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_operator_image_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR)

        operator_image = (
            session.query(OperatorImage).filter(OperatorImage.id == id).first()
        )
        if operator_image is not None:
            operator_image_data = delete_operator_image(session, operator_image)
            log_event(token, request_info, operator_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_PICTURE,
    tags=["Account Image"],
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
async def upload_operator_image(
    form_param: CreateFormForOP = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        if form_param.operator_id is None:
            form_param.operator_id = token.operator_id
        is_self_update = form_param.operator_id == token.operator_id
        if not is_self_update:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)

        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)
        operator_image = OperatorImage(
            company_id=token.company_id,
            operator_id=form_param.operator_id,
            file_name=form_param.file.filename,
            file_type=form_param.file.content_type,
            file_size=len(file_bytes),
        )
        operator_image_data = create_operator_image(session, operator_image, file_bytes)
        log_event(token, request_info, operator_image_data)
        return operator_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes an operator image.**    
            - Operator must have a valid access token.    
            - Operators can delete their own image without additional permissions.    
            - To delete another operator's image, the `operator.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.      
        """
    ),
)
async def delete_operator_image(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        operator_image = (
            session.query(OperatorImage)
            .filter(
                OperatorImage.id == id, OperatorImage.company_id == token.company_id
            )
            .first()
        )
        if operator_image.operator_id != token.operator_id:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)

        operator_image_data = delete_operator_image(session, operator_image)
        log_event(token, request_info, operator_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
