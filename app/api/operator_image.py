"""
Operator Image API Router for EnteBus.

Provides endpoints for managing operator images, including creation,
deletion, and retrieval. Uses Pydantic schemas for
input validation and structured output.
"""

from datetime import datetime
from fastapi import APIRouter, Form, File, UploadFile, Response, status, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from io import BytesIO
from typing import List
from enum import StrEnum
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.src.db import (
    Company,
    OperatorImage,
    OperatorToken,
    SessionLocal,
    ExecutiveToken,
    Operator,
)
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src import exceptions
from app.src.urls import URL_OPERATOR_PICTURE
from app.src.minio import delete_file, upload_file, download_file
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token, validate_id, validate_image
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    apply_created_on_filters,
    apply_id_filters,
    apply_picture_filters,
    enum_str,
    resize_image,
)
from app.src.constants import (
    MAX_IMAGE_RESOLUTION,
    MAX_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
)
from app.src.buckets import OPERATOR_IMAGES
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    PictureFilter,
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


class CreateFormForEX(ImageUploadForm):
    """Form data for creating a new operator image for an executive."""

    company_id: int = Field(Form())
    operator_id: int = Field(Form())


class CreateFormForOP(ImageUploadForm):
    """Form data for creating a new operator image for an operator."""

    operator_id: int | None = Field(Form(default=None))


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator image."""

    pass


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParamsForOP(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for operators."""

    operator_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForOP):
    """Query parameters for executives."""

    company_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


class ImageQueryParams(BaseModel):
    """Query parameters for retrieving an operator image."""

    width: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )
    height: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )


# Functions
def create_image(session: Session, form_param: CreateForm, file_bytes: bytes) -> dict:
    """
    Creates a new operator image record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating an operator image.
        file_bytes (bytes): The image file bytes.

    Returns:
        dict: The created operator image data.
    """
    operator_image = OperatorImage(
        company_id=form_param.company_id,
        operator_id=form_param.operator_id,
        file_name=form_param.file.filename,
        file_type=form_param.file.content_type,
        file_size=len(file_bytes),
    )
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


def delete_image(
    session: Session,
    operator_image: OperatorImage,
) -> dict:
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


def search_image(session: Session, query_params: QueryParams) -> list[OperatorImage]:
    """
    Search for operator images based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operator images that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[OperatorImage]: List of OperatorImage instances that match the search criteria.
    """
    query = session.query(OperatorImage)
    if query_params.company_id is not None:
        query = query.filter(OperatorImage.company_id == query_params.company_id)
    if query_params.operator_id is not None:
        query = query.filter(OperatorImage.operator_id == query_params.operator_id)

    # Generalized filters
    query = apply_id_filters(query, OperatorImage, query_params)
    query = apply_created_on_filters(query, OperatorImage, query_params)
    query = apply_picture_filters(query, OperatorImage, query_params)

    # Ordering and pagination
    ordering_attr = getattr(OperatorImage, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    operator_images = query.all()
    return operator_images


def download_image(
    operator_image: OperatorImage, query_params: ImageQueryParams
) -> StreamingResponse:
    """
    Download an operator image by its ID.

    This function retrieves the operator image metadata from the database and
    then fetches the corresponding image file from the MinIO bucket.

    Args:
        operator_image (OperatorImage): The OperatorImage instance to download.
        query_params (ImageQueryParams): Query parameters for image resizing.

    Returns:
        StreamingResponse: A StreamingResponse containing the downloaded image.

    Raises:
        exceptions.UnknownValue: If no operator image with the specified ID is found.
    """
    if operator_image is not None:
        file_bytes = download_file(OPERATOR_IMAGES, str(operator_image.id))
        if query_params.width is not None or query_params.height is not None:
            file_bytes = resize_image(
                file_bytes,
                width=query_params.width,
                height=query_params.height,
            )

        return StreamingResponse(
            BytesIO(file_bytes),
            media_type=operator_image.file_type,
            headers={
                "Content-Disposition": f'inline; filename="{operator_image.file_name}"',
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
    raise exceptions.UnknownValue(OperatorImage.id)


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
            exceptions.UnknownValue(OperatorImage.operator_id),
            exceptions.UnknownValue(OperatorImage.company_id),
            exceptions.InvalidAssociation(
                OperatorImage.operator_id, OperatorImage.company_id
            ),
        ]
    ),
    description=(
        """
            **Uploads an operator image.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.operator.update` permission to upload other operator images.    
        """
    ),
)
async def upload_operator_image_for_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR)

        validate_id(session, Company, form_param.company_id, OperatorImage.company_id)
        operator = validate_id(
            session, Operator, form_param.operator_id, OperatorImage.operator_id
        )
        if operator.company_id != form_param.company_id:
            raise exceptions.InvalidAssociation(
                OperatorImage.operator_id, OperatorImage.company_id
            )

        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        operator_image_data = create_image(
            session, CreateForm(**form_param.model_dump()), file_bytes
        )
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
            - Executive must have a valid access token.       
            - To delete operator's image, the `company.operator.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_operator_image_for_executive(
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
            operator_image_data = delete_image(session, operator_image)
            log_event(token, request_info, operator_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_OPERATOR_PICTURE,
    tags=["Operator Account Image"],
    response_model=List[OperatorImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator images.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_operator_image_for_executive(
    query_params: QueryParamsForEX = Depends(), access_token=Depends(oauth2_executive)
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        return search_image(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    tags=["Operator Account Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(OperatorImage.id)]
    ),
    description=(
        """
            **Download operator profile picture in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_operator_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        operator_image = (
            session.query(OperatorImage).filter(OperatorImage.id == id).first()
        )
        return download_image(operator_image, query_params)
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
            exceptions.UnknownValue(OperatorImage.operator_id),
        ]
    ),
    description=(
        """
            **Uploads an operator image.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.operator.update` permission to upload other operator images.    
            - Operator can update their own image without permission.    
        """
    ),
)
async def upload_operator_image_for_operator(
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

        validate_id(
            session,
            Operator,
            form_param.operator_id,
            OperatorImage.operator_id,
            extra_filter=Operator.company_id == token.company_id,
        )
        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        operator_image_data = create_image(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            file_bytes,
        )
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
            - To delete another operator's image, the `company.operator.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_operator_image_for_operator(
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
        if operator_image is None or operator_image.operator_id != token.operator_id:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)
        if operator_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        operator_image_data = delete_image(session, operator_image)
        log_event(token, request_info, operator_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_OPERATOR_PICTURE,
    tags=["Account Image"],
    response_model=List[OperatorImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of operator images.**    
            - Requires a valid access token for authentication.    
            - Only operator images belonging to the same company as the logged-in operator will be returned.    
        """
    ),
)
async def fetch_operator_image_for_operator(
    query_params: QueryParamsForOP = Depends(), access_token=Depends(bearer_operator)
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        return search_image(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    tags=["Account Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(OperatorImage.id)]
    ),
    description=(
        """
            **Download operator profile picture in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_operator_image_for_operator(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_operator),
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
        return download_image(operator_image, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
