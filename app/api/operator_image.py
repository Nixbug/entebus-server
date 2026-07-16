"""
Operator Image API router.

Provides endpoints for managing operator images:
    - POST (executive, operator)
    - DELETE (executive, operator)
    - GET (executive, operator)
    - GET /{id} (executive, operator)
"""

from datetime import datetime
from enum import StrEnum
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_operator, oauth2_executive
from app.src import exceptions, schemas
from app.src.buckets import OPERATOR_IMAGES
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)
from app.src.db import (
    ExecutiveToken,
    Operator,
    OperatorImage,
    OperatorToken,
    get_db_session,
)
from app.src.description import Description
from app.src.enums import OrderIn
from app.src.filters import CreatedOnFilter, IDFilter, PaginationFilter, PictureFilter
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_picture_filters,
    enum_str,
    fuse_exception_responses,
    get_by_id,
    get_operator_roles,
    get_request_info,
    resize_image,
)
from app.src.minio import delete_file, download_file, upload_file
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.urls import URL_OPERATOR_PICTURE
from app.src.validators import (
    authorize_executive,
    validate_id,
    validate_image,
    verify_permission,
    verify_token,
)

route_executive = APIRouter()
route_operator = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class OperatorImageSchema(BaseModel):
    """Schema for operator image response."""

    id: int
    company_id: int
    operator_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
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

    operator_id: int = Field(Form())


class CreateFormForOP(ImageUploadForm):
    """Form data for creating a new operator image for an operator."""

    operator_id: int | None = Field(Form(default=None))


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new operator image."""

    pass


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
async def create_operator_image(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
    operator_filter=None,
) -> dict:
    """
    Create a new operator image in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new operator image.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        operator_filter: Additional filter for validating operator ownership.

    Returns:
        dict: Created operator image data.
    """
    operator = validate_id(
        session,
        Operator,
        form_param.operator_id,
        OperatorImage.operator_id,
        extra_filter=operator_filter,
    )

    file_bytes = await form_param.file.read()
    filename = form_param.file.filename
    if not filename:
        raise exceptions.InvalidValue("filename")
    validate_image(file_bytes, filename)

    content_type = form_param.file.content_type
    if not content_type:
        raise exceptions.InvalidValue("content_type")

    operator_image = OperatorImage(
        company_id=operator.company_id,
        operator_id=operator.id,
        file_name=filename,
        file_type=content_type,
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
    log_event(token, request_info, operator_image_data)
    return operator_image_data


def delete_operator_image(
    session: Session,
    operator_image: OperatorImage,
    token: ExecutiveToken | OperatorToken,
    request_info: schemas.RequestInfo,
):
    """
    Delete an operator image from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        operator_image (OperatorImage): Operator image instance to delete.
        token (ExecutiveToken | OperatorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    operator_image_data = jsonable_encoder(operator_image)
    session.delete(operator_image)
    session.commit()
    delete_file(OPERATOR_IMAGES, str(operator_image.id))
    log_event(token, request_info, operator_image_data)


def search_operator_images(
    session: Session, query_params: QueryParams
) -> list[OperatorImage]:
    """
    Search for operator images based on provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve operator images that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[OperatorImage]: List of operator images that match the search criteria.
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


def fetch_operator_image(
    session: Session, id: int, query_params: ImageQueryParams, image_filter=None
) -> StreamingResponse:
    """
    Fetch an operator image by its ID and optionally resize it.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the operator image to fetch.
        query_params (ImageQueryParams): Query parameters for resizing the image.
        image_filter: Additional filter for restricting image access.

    Returns:
        StreamingResponse: The operator image stream in original or resized form.
    """
    operator_image = get_by_id(session, OperatorImage, id, extra_filter=image_filter)
    if operator_image is None:
        raise exceptions.UnknownValue(OperatorImage.id)

    file_bytes = download_file(OPERATOR_IMAGES, str(operator_image.id))
    assert file_bytes is not None, "Downloaded file bytes should not be None"
    resized_bytes = resize_image(
        file_bytes,
        width=query_params.width,
        height=query_params.height,
    )
    return StreamingResponse(
        BytesIO(resized_bytes),
        media_type=operator_image.file_type,
        headers={
            "Content-Disposition": f'inline; filename="{operator_image.file_name}"',
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
    exceptions.UnknownValue(OperatorImage.operator_id),
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

DOWNLOAD_EXCEPTIONS = [
    exceptions.InvalidToken(),
    exceptions.UnknownValue(OperatorImage.id),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = Description().add_head("Uploads an operator image.")

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes an operator image.")
    .add_line("Returns 204 No Content even if the specified image does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of operator images.")

DOWNLOAD_DESCRIPTION = Description().add_head(
    "Downloads operator profile picture in original or resized resolution."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_OPERATOR_PICTURE,
    summary="Create operator image",
    tags=["Operator Account Image"],
    response_model=OperatorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.operator.update` permission.")
        .to_string()
    ),
)
async def upload_operator_image_for_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR],
        )
        return await create_operator_image(
            session,
            CreateForm(**form_param.model_dump()),
            token,
            request_info,
            form_param.operator_id,
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    summary="Delete operator image",
    tags=["Operator Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `company.operator.update` permission.")
        .to_string()
    ),
)
async def delete_operator_image_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_COMPANY_OPERATOR],
        )
        operator_image = get_by_id(session, OperatorImage, id)
        if operator_image is not None:
            delete_operator_image(session, operator_image, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_OPERATOR_PICTURE,
    summary="Fetch operator image",
    tags=["Operator Account Image"],
    response_model=list[OperatorImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_operator_images_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_operator_images(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    summary="Download operator image",
    tags=["Operator Account Image"],
    responses=fuse_exception_responses(DOWNLOAD_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_operator_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return fetch_operator_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_OPERATOR_PICTURE,
    summary="Create operator image",
    tags=["Account Image"],
    response_model=OperatorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in operator must have `company.operator.update` permission to upload other operator images."
        )
        .add_line("Operator can update their own image without permission.")
        .to_string()
    ),
)
async def upload_operator_image_for_operator(
    form_param: CreateFormForOP = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        operator_id = form_param.operator_id or token.operator_id
        if operator_id != token.operator_id:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)

        return await create_operator_image(
            session,
            CreateForm(
                **form_param.model_dump(exclude={"operator_id"}),
                operator_id=operator_id,
            ),
            token,
            request_info,
            operator_filter=(Operator.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.delete(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    summary="Delete operator image",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line(
            "Operators can delete their own image without additional permissions."
        )
        .add_line(
            "To delete another operator's image, the `company.operator.update` permission is required."
        )
        .to_string()
    ),
)
async def delete_operator_image_for_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        operator_image = get_by_id(
            session,
            OperatorImage,
            id,
            extra_filter=(OperatorImage.company_id == token.company_id),
        )
        if operator_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if operator_image.operator_id != token.operator_id:
            roles = get_operator_roles(session, token)
            verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_OPERATOR)

        delete_operator_image(session, operator_image, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    URL_OPERATOR_PICTURE,
    summary="Fetch operator image",
    tags=["Account Image"],
    response_model=list[OperatorImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Only operator images belonging to the same company as the logged-in operator will be returned."
        )
        .to_string()
    ),
)
async def fetch_operator_images_for_operator(
    query_params: QueryParamsForOP = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return search_operator_images(
            session,
            QueryParams(**query_params.model_dump(), company_id=token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_operator.get(
    f"{URL_OPERATOR_PICTURE}/{{id}}",
    summary="Download operator image",
    tags=["Account Image"],
    responses=fuse_exception_responses(DOWNLOAD_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_operator_image_for_operator(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_operator),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, OperatorToken, access_token.credentials)
        return fetch_operator_image(
            session,
            id,
            query_params,
            image_filter=(OperatorImage.company_id == token.company_id),
        )
    except Exception as e:
        exceptions.handle(e)
