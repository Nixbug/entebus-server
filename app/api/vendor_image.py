"""
Vendor Image API router.

Provides endpoints for managing vendor images:
    - POST (executive, vendor)
    - DELETE (executive, vendor)
    - GET (executive, vendor)
    - GET /{id} (executive, vendor)
"""

from datetime import datetime
from enum import StrEnum
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement
from sqlalchemy.orm.session import Session

from app.api.bearer import bearer_vendor, oauth2_executive
from app.src import exceptions, schemas
from app.src.buckets import VENDOR_IMAGES
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)
from app.src.db import (
    ExecutiveToken,
    Vendor,
    VendorImage,
    VendorToken,
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
    get_request_info,
    get_vendor_roles,
    resize_image,
)
from app.src.minio import delete_file, download_file, upload_file
from app.src.openobserve import log_event
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.urls import URL_VENDOR_PICTURE
from app.src.validators import (
    authorize_executive,
    validate_id,
    validate_image,
    verify_permission,
    verify_token,
)

route_executive = APIRouter()
route_vendor = APIRouter()


# ---------------------------------------------------------------------------
## Output Schema
# ---------------------------------------------------------------------------
class VendorImageSchema(BaseModel):
    """Schema for vendor image response."""

    id: int
    business_id: int
    vendor_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


# ---------------------------------------------------------------------------
## Input Forms
# ---------------------------------------------------------------------------
class ImageUploadForm(BaseModel):
    """Form data for uploading a vendor image."""

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
    """Form data for creating a new vendor image for an executive."""

    vendor_id: int = Field(Form())


class CreateFormForVE(ImageUploadForm):
    """Form data for creating a new vendor image for a vendor."""

    vendor_id: int | None = Field(Form(default=None))


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vendor image."""

    pass


# ---------------------------------------------------------------------------
## Query Parameters
# ---------------------------------------------------------------------------
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParamsForVE(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for vendors."""

    vendor_id: int | None = Field(Query(default=None))
    order_by: OrderBy = Field(Query(default=OrderBy.ID, description=enum_str(OrderBy)))
    order_in: OrderIn = Field(
        Query(default=OrderIn.DESCENDING, description=enum_str(OrderIn))
    )


class QueryParamsForEX(QueryParamsForVE):
    """Query parameters for executives."""

    business_id: int | None = Field(Query(default=None))


class QueryParams(QueryParamsForEX):
    """General combined query parameters."""

    pass


class ImageQueryParams(BaseModel):
    """Query parameters for retrieving a vendor image."""

    width: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )
    height: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )


# ---------------------------------------------------------------------------
## Core Functions
# ---------------------------------------------------------------------------
async def create_vendor_image(
    session: Session,
    form_param: CreateForm,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
    vendor_filter: ColumnElement[bool] | None = None,
) -> dict:
    """
    Create a new vendor image in the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a new vendor image.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
        vendor_filter: Additional filter for validating vendor ownership.

    Returns:
        dict: Created vendor image data.
    """
    vendor = validate_id(
        session,
        Vendor,
        form_param.vendor_id,
        VendorImage.vendor_id,
        extra_filter=vendor_filter,
    )

    file_bytes = await form_param.file.read()
    filename = form_param.file.filename
    if not filename:
        raise exceptions.InvalidValue("filename")
    validate_image(file_bytes, filename)

    content_type = form_param.file.content_type
    if not content_type:
        raise exceptions.InvalidValue("content_type")

    vendor_image = VendorImage(
        business_id=vendor.business_id,
        vendor_id=vendor.id,
        file_name=filename,
        file_type=content_type,
        file_size=len(file_bytes),
    )
    session.add(vendor_image)
    session.flush()
    upload_file(
        VENDOR_IMAGES,
        str(vendor_image.id),
        len(file_bytes),
        BytesIO(file_bytes),
    )
    session.commit()
    session.refresh(vendor_image)

    vendor_image_data = jsonable_encoder(vendor_image)
    log_event(token, request_info, vendor_image_data)
    return vendor_image_data


def delete_vendor_image(
    session: Session,
    vendor_image: VendorImage,
    token: ExecutiveToken | VendorToken,
    request_info: schemas.RequestInfo,
):
    """
    Delete a vendor image from the database.

    Args:
        session (Session): Active SQLAlchemy database session.
        vendor_image (VendorImage): Vendor image instance to delete.
        token (ExecutiveToken | VendorToken): Authenticated token.
        request_info (schemas.RequestInfo): Request information for logging.
    """
    vendor_image_data = jsonable_encoder(vendor_image)
    session.delete(vendor_image)
    session.commit()
    delete_file(VENDOR_IMAGES, str(vendor_image.id))
    log_event(token, request_info, vendor_image_data)


def search_vendor_images(
    session: Session, query_params: QueryParams
) -> list[VendorImage]:
    """
    Search for vendor images based on provided query parameters.

    This function supports multiple filtering, ordering, and pagination capabilities
    to retrieve vendor images that match various criteria.

    Args:
        session (Session): Active SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        list[VendorImage]: List of vendor images that match the search criteria.
    """
    query = session.query(VendorImage)
    if query_params.business_id is not None:
        query = query.filter(VendorImage.business_id == query_params.business_id)
    if query_params.vendor_id is not None:
        query = query.filter(VendorImage.vendor_id == query_params.vendor_id)

    # Generalized filters
    query = apply_id_filters(query, VendorImage, query_params)
    query = apply_created_on_filters(query, VendorImage, query_params)
    query = apply_picture_filters(query, VendorImage, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VendorImage, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vendor_images = query.all()
    return vendor_images


def fetch_vendor_image(
    session: Session,
    id: int,
    query_params: ImageQueryParams,
    image_filter: ColumnElement[bool] | None = None,
) -> StreamingResponse:
    """
    Fetch a vendor image by its ID and optionally resize it.

    Args:
        session (Session): Active SQLAlchemy database session.
        id (int): ID of the vendor image to fetch.
        query_params (ImageQueryParams): Query parameters for resizing the image.
        image_filter: Additional filter for restricting image access.

    Returns:
        StreamingResponse: The vendor image stream in original or resized form.
    """
    vendor_image = get_by_id(session, VendorImage, id, extra_filter=image_filter)
    if vendor_image is None:
        raise exceptions.UnknownValue(VendorImage.id)

    file_bytes = download_file(VENDOR_IMAGES, str(vendor_image.id))
    assert file_bytes is not None, "Downloaded file bytes should not be None"
    resized_bytes = resize_image(
        file_bytes,
        width=query_params.width,
        height=query_params.height,
    )
    return StreamingResponse(
        BytesIO(resized_bytes),
        media_type=vendor_image.file_type,
        headers={
            "Content-Disposition": f'inline; filename="{vendor_image.file_name}"',
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
    exceptions.UnknownValue(VendorImage.vendor_id),
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
    exceptions.UnknownValue(VendorImage.id),
]


# ---------------------------------------------------------------------------
## Common description
# ---------------------------------------------------------------------------
POST_DESCRIPTION = Description().add_head("Uploads a vendor image.")

DELETE_DESCRIPTION = (
    Description()
    .add_head("Deletes a vendor image.")
    .add_line("Returns 204 No Content even if the specified image does not exist.")
)

GET_DESCRIPTION = Description().add_head("Fetches a list of vendor images.")

DOWNLOAD_DESCRIPTION = Description().add_head(
    "Downloads vendor profile picture in original or resized resolution."
)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_PICTURE,
    summary="Create vendor image",
    tags=["Vendor Account Image"],
    response_model=VendorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.vendor.update` permission.")
        .to_string()
    ),
)
async def upload_vendor_image_for_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR],
        )
        return await create_vendor_image(
            session, CreateForm(**form_param.model_dump()), token, request_info
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.delete(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    summary="Delete vendor image",
    tags=["Vendor Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Logged-in executive must have `business.vendor.update` permission.")
        .to_string()
    ),
)
async def delete_vendor_image_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = authorize_executive(
            session,
            access_token,
            [ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR],
        )
        vendor_image = get_by_id(session, VendorImage, id)
        if vendor_image is not None:
            delete_vendor_image(session, vendor_image, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    URL_VENDOR_PICTURE,
    summary="Fetch vendor image",
    tags=["Vendor Account Image"],
    response_model=list[VendorImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(GET_DESCRIPTION.to_string()),
)
async def fetch_vendor_images_for_executive(
    query_params: QueryParamsForEX = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return search_vendor_images(
            session,
            QueryParams(**query_params.model_dump()),
        )
    except Exception as e:
        exceptions.handle(e)


@route_executive.get(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    summary="Download vendor image",
    tags=["Vendor Account Image"],
    responses=fuse_exception_responses(DOWNLOAD_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_vendor_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
    session: Session = Depends(get_db_session),
):
    try:
        verify_token(session, ExecutiveToken, access_token)
        return fetch_vendor_image(session, id, query_params)
    except Exception as e:
        exceptions.handle(e)


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_PICTURE,
    summary="Create vendor image",
    tags=["Account Image"],
    response_model=VendorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(POST_EXCEPTIONS),
    description=(
        POST_DESCRIPTION.copy()
        .add_line(
            "Logged-in vendor must have `business.vendor.update` permission to upload other vendor images."
        )
        .add_line("Vendor can update their own image without permission.")
        .to_string()
    ),
)
async def upload_vendor_image_for_vendor(
    form_param: CreateFormForVE = Depends(),
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        vendor_id = form_param.vendor_id or token.vendor_id
        if vendor_id != token.vendor_id:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)

        return await create_vendor_image(
            session,
            CreateForm(
                **form_param.model_dump(exclude={"vendor_id"}), vendor_id=vendor_id
            ),
            token,
            request_info,
            vendor_filter=(Vendor.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.delete(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    summary="Delete vendor image",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(DELETE_EXCEPTIONS),
    description=(
        DELETE_DESCRIPTION.copy()
        .add_line("Vendors can delete their own image without additional permissions.")
        .add_line(
            "To delete another vendor's image, the `business.vendor.update` permission is required."
        )
        .to_string()
    ),
)
async def delete_vendor_image_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        vendor_image = get_by_id(
            session,
            VendorImage,
            id,
            extra_filter=(VendorImage.business_id == token.business_id),
        )
        if vendor_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if vendor_image.vendor_id != token.vendor_id:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)

        delete_vendor_image(session, vendor_image, token, request_info)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    URL_VENDOR_PICTURE,
    summary="Fetch vendor image",
    tags=["Account Image"],
    response_model=list[VendorImageSchema],
    responses=fuse_exception_responses(GET_EXCEPTIONS),
    description=(
        GET_DESCRIPTION.copy()
        .add_line(
            "Only vendor images belonging to the same business as the logged-in vendor will be returned."
        )
        .to_string()
    ),
)
async def fetch_vendor_images_for_vendor(
    query_params: QueryParamsForVE = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        return search_vendor_images(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)


@route_vendor.get(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    summary="Download vendor image",
    tags=["Account Image"],
    responses=fuse_exception_responses(DOWNLOAD_EXCEPTIONS),
    description=(DOWNLOAD_DESCRIPTION.to_string()),
)
async def download_vendor_image_for_vendor(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_vendor),
    session: Session = Depends(get_db_session),
):
    try:
        token = verify_token(session, VendorToken, access_token.credentials)
        return fetch_vendor_image(
            session,
            id,
            query_params,
            image_filter=(VendorImage.business_id == token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
