"""
Vendor Image API Router for EnteBus.

Provides endpoints for managing vendor images, including creation,
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
    Business,
    VendorImage,
    VendorToken,
    SessionLocal,
    ExecutiveToken,
    Vendor,
)
from app.api.bearer import oauth2_executive, bearer_vendor
from app.src.permissions.vendor import PermissionPath as VendorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src import exceptions
from app.src.urls import URL_VENDOR_PICTURE
from app.src.minio import delete_file, upload_file, download_file
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token, validate_id, validate_image
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    get_vendor_roles,
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
from app.src.buckets import VENDOR_IMAGES
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    PictureFilter,
)

route_executive = APIRouter()
route_vendor = APIRouter()


## Output Schema
class VendorImageSchema(BaseModel):
    """Schema for vendor image response."""

    id: int
    business_id: int
    vendor_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


## Input Forms
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

    business_id: int = Field(Form())
    vendor_id: int = Field(Form())


class CreateFormForVE(ImageUploadForm):
    """Form data for creating a new vendor image for a vendor."""

    vendor_id: int | None = Field(Form(default=None))


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vendor image."""

    pass


## Query Parameters
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


# Functions
def create_image(session: Session, form_param: CreateForm, file_bytes: bytes) -> dict:
    """
    Creates a new vendor image record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a vendor image.
        file_bytes (bytes): The image file bytes.

    Returns:
        dict: The created vendor image data.
    """
    vendor_image = VendorImage(
        business_id=form_param.business_id,
        vendor_id=form_param.vendor_id,
        file_name=form_param.file.filename,
        file_type=form_param.file.content_type,
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
    return vendor_image_data


def delete_image(
    session: Session,
    vendor_image: VendorImage,
) -> dict:
    """
    Deletes a vendor image and its associated file from storage.

    Args:
        session (Session): SQLAlchemy database session.
        vendor_image (VendorImage): Vendor image to delete.

    Returns:
        dict: deleted vendor image data for logging purposes.
    """
    vendor_image_data = jsonable_encoder(vendor_image)
    session.delete(vendor_image)
    session.commit()
    delete_file(VENDOR_IMAGES, str(vendor_image.id))
    return vendor_image_data


def search_image(session: Session, query_params: QueryParams) -> list[VendorImage]:
    """
    Search for vendor images based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vendor images that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[VendorImage]: List of VendorImage instances that match the search criteria.
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


def download_image(
    vendor_image: VendorImage, query_params: ImageQueryParams
) -> StreamingResponse:
    """
    Download a vendor image by its ID.

    This function retrieves the vendor image metadata from the database and
    then fetches the corresponding image file from the MinIO bucket.

    Args:
        vendor_image (VendorImage): The VendorImage instance to download.
        query_params (ImageQueryParams): Query parameters for image resizing.

    Returns:
        StreamingResponse: A StreamingResponse containing the downloaded image.

    Raises:
        exceptions.UnknownValue: If no vendor image with the specified ID is found.
    """
    if vendor_image is not None:
        file_bytes = download_file(VENDOR_IMAGES, str(vendor_image.id))
        if query_params.width is not None or query_params.height is not None:
            file_bytes = resize_image(
                file_bytes,
                width=query_params.width,
                height=query_params.height,
            )

        return StreamingResponse(
            BytesIO(file_bytes),
            media_type=vendor_image.file_type,
            headers={
                "Content-Disposition": f'inline; filename="{vendor_image.file_name}"',
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
    raise exceptions.UnknownValue(VendorImage.id)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VENDOR_PICTURE,
    tags=["Vendor Account Image"],
    response_model=VendorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidImageFile(),
            exceptions.UnknownValue(VendorImage.vendor_id),
            exceptions.UnknownValue(VendorImage.business_id),
            exceptions.InvalidAssociation(
                VendorImage.vendor_id, VendorImage.business_id
            ),
        ]
    ),
    description=(
        """
            **Uploads a vendor image.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `business.vendor.update` permission to upload other vendor images.    
        """
    ),
)
async def upload_vendor_image_for_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR)

        validate_id(session, Business, form_param.business_id, VendorImage.business_id)
        vendor = validate_id(
            session, Vendor, form_param.vendor_id, VendorImage.vendor_id
        )
        if vendor.business_id != form_param.business_id:
            raise exceptions.InvalidAssociation(
                VendorImage.vendor_id, VendorImage.business_id
            )

        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        vendor_image_data = create_image(
            session, CreateForm(**form_param.model_dump()), file_bytes
        )
        log_event(token, request_info, vendor_image_data)
        return vendor_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    tags=["Vendor Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vendor image.**    
            - Executive must have a valid access token.       
            - To delete a vendor's image, the `business.vendor.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_vendor_image_for_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_BUSINESS_VENDOR)

        vendor_image = session.query(VendorImage).filter(VendorImage.id == id).first()
        if vendor_image is not None:
            vendor_image_data = delete_image(session, vendor_image)
            log_event(token, request_info, vendor_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_VENDOR_PICTURE,
    tags=["Vendor Account Image"],
    response_model=List[VendorImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vendor images.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_vendor_image_for_executive(
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
    f"{URL_VENDOR_PICTURE}/{{id}}",
    tags=["Vendor Account Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(VendorImage.id)]
    ),
    description=(
        """
            **Download vendor profile picture in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_vendor_image_for_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        vendor_image = session.query(VendorImage).filter(VendorImage.id == id).first()
        return download_image(vendor_image, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Vendor]
# ---------------------------------------------------------------------------
@route_vendor.post(
    URL_VENDOR_PICTURE,
    tags=["Account Image"],
    response_model=VendorImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidImageFile(),
            exceptions.UnknownValue(VendorImage.vendor_id),
        ]
    ),
    description=(
        """
            **Uploads a vendor image.**    
            - Vendor must have a valid access token.    
            - Logged-in vendor must have `business.vendor.update` permission to upload other vendor images.    
            - Vendor can update their own image without permission.    
        """
    ),
)
async def upload_vendor_image_for_vendor(
    form_param: CreateFormForVE = Depends(),
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        if form_param.vendor_id is None:
            form_param.vendor_id = token.vendor_id
        is_self_update = form_param.vendor_id == token.vendor_id
        if not is_self_update:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)

        validate_id(
            session,
            Vendor,
            form_param.vendor_id,
            VendorImage.vendor_id,
            extra_filter=Vendor.business_id == token.business_id,
        )
        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        vendor_image_data = create_image(
            session,
            CreateForm(**form_param.model_dump(), business_id=token.business_id),
            file_bytes,
        )
        log_event(token, request_info, vendor_image_data)
        return vendor_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.delete(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    tags=["Account Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vendor image.**    
            - Vendor must have a valid access token.    
            - Vendors can delete their own image without additional permissions.    
            - To delete another vendor's image, the `business.vendor.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_vendor_image_for_vendor(
    id: int,
    access_token=Depends(bearer_vendor),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        vendor_image = (
            session.query(VendorImage)
            .filter(VendorImage.id == id, VendorImage.business_id == token.business_id)
            .first()
        )
        if vendor_image is None or vendor_image.vendor_id != token.vendor_id:
            roles = get_vendor_roles(session, token)
            verify_permission(roles, VendorPermissionPath.UPDATE_BUSINESS_VENDOR)
        if vendor_image is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        vendor_image_data = delete_image(session, vendor_image)
        log_event(token, request_info, vendor_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    URL_VENDOR_PICTURE,
    tags=["Account Image"],
    response_model=List[VendorImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vendor images.**    
            - Requires a valid access token for authentication.    
            - Only vendor images belonging to the same business as the logged-in vendor will be returned.    
        """
    ),
)
async def fetch_vendor_image_for_vendor(
    query_params: QueryParamsForVE = Depends(), access_token=Depends(bearer_vendor)
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        return search_image(
            session,
            QueryParams(**query_params.model_dump(), business_id=token.business_id),
        )
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_vendor.get(
    f"{URL_VENDOR_PICTURE}/{{id}}",
    tags=["Account Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(VendorImage.id)]
    ),
    description=(
        """
            **Download vendor profile picture in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_vendor_image_for_vendor(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_vendor),
):
    try:
        session = SessionLocal()
        token = verify_token(session, VendorToken, access_token.credentials)

        vendor_image = (
            session.query(VendorImage)
            .filter(VendorImage.id == id, VendorImage.business_id == token.business_id)
            .first()
        )
        return download_image(vendor_image, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
