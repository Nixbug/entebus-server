"""
Vehicle Image API Router for EnteBus.

Provides endpoints for managing vehicle images, including creation,
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
    VehicleImage,
    OperatorToken,
    SessionLocal,
    ExecutiveToken,
    Vehicle,
)
from app.api.bearer import oauth2_executive, bearer_operator
from app.src.permissions.operator import PermissionPath as OperatorPermissionPath
from app.src.permissions.executive import PermissionPath as ExecutivePermissionPath
from app.src import exceptions
from app.src.urls import URL_VEHICLE_PICTURE
from app.src.minio import delete_file, upload_file, download_file
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token, validate_id
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    get_operator_roles,
    validate_image,
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
from app.src.buckets import VEHICLE_IMAGES
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
class VehicleImageSchema(BaseModel):
    """Schema for vehicle image response."""

    id: int
    company_id: int
    vehicle_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


## Input Forms
class ImageUploadForm(BaseModel):
    """Form data for uploading a vehicle image."""

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
    """Form data for creating a new vehicle image for an executive."""

    company_id: int = Field(Form())
    vehicle_id: int = Field(Form())


class CreateFormForOP(ImageUploadForm):
    """Form data for creating a new vehicle image for an operator."""

    vehicle_id: int = Field(Form())


class CreateForm(CreateFormForEX):
    """Generic combined form data for creating a new vehicle image."""

    pass


## Query Parameters
class OrderBy(StrEnum):
    """Enum for ordering results."""

    ID = "id"
    CREATED_ON = "created_on"
    FILE_SIZE = "file_size"


class QueryParamsForOP(PictureFilter, CreatedOnFilter, IDFilter, PaginationFilter):
    """Query parameters for operators."""

    vehicle_id: int | None = Field(Query(default=None))
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
    """Query parameters for retrieving a vehicle image."""

    width: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )
    height: int | None = Field(
        Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION)
    )


# Functions
def create_image(session: Session, form_param: CreateForm, file_bytes: bytes) -> dict:
    """
    Creates a new vehicle image record in the database.

    Args:
        session (Session): SQLAlchemy database session.
        form_param (CreateForm): Form data for creating a vehicle image.
        file_bytes (bytes): The image file bytes.

    Returns:
        dict: The created vehicle image data.
    """
    vehicle_image = VehicleImage(
        company_id=form_param.company_id,
        vehicle_id=form_param.vehicle_id,
        file_name=form_param.file.filename,
        file_type=form_param.file.content_type,
        file_size=len(file_bytes),
    )
    session.add(vehicle_image)
    session.flush()
    upload_file(
        VEHICLE_IMAGES,
        str(vehicle_image.id),
        len(file_bytes),
        BytesIO(file_bytes),
    )
    session.commit()
    session.refresh(vehicle_image)
    vehicle_image_data = jsonable_encoder(vehicle_image)
    return vehicle_image_data


def delete_image(
    session: Session,
    vehicle_image: VehicleImage,
) -> dict:
    """
    Deletes a vehicle image and its associated file from storage.

    Args:
        session (Session): SQLAlchemy database session.
        vehicle_image (VehicleImage): Vehicle image to delete.

    Returns:
        dict: deleted vehicle image data for logging purposes.
    """
    vehicle_image_data = jsonable_encoder(vehicle_image)
    session.delete(vehicle_image)
    session.commit()
    delete_file(VEHICLE_IMAGES, str(vehicle_image.id))
    return vehicle_image_data


def search_image(session: Session, query_params: QueryParams) -> list[VehicleImage]:
    """
    Search for vehicle images based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve vehicle images that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[VehicleImage]: List of VehicleImage instances that match the search criteria.
    """
    query = session.query(VehicleImage)
    if query_params.company_id is not None:
        query = query.filter(VehicleImage.company_id == query_params.company_id)
    if query_params.vehicle_id is not None:
        query = query.filter(VehicleImage.vehicle_id == query_params.vehicle_id)

    # Generalized filters
    query = apply_id_filters(query, VehicleImage, query_params)
    query = apply_created_on_filters(query, VehicleImage, query_params)
    query = apply_picture_filters(query, VehicleImage, query_params)

    # Ordering and pagination
    ordering_attr = getattr(VehicleImage, query_params.order_by.value)
    ordering_func = (
        ordering_attr.asc
        if query_params.order_in == OrderIn.ASCENDING
        else ordering_attr.desc
    )
    query = query.order_by(ordering_func())
    query = query.offset(query_params.offset).limit(query_params.limit)

    vehicle_images = query.all()
    return vehicle_images


def download_image(
    vehicle_image: VehicleImage, query_params: ImageQueryParams
) -> StreamingResponse:
    """
    Download a vehicle image by its ID.

    This function retrieves the vehicle image metadata from the database and
    then fetches the corresponding image file from the MinIO bucket.

    Args:
        vehicle_image (VehicleImage): The VehicleImage instance to download.
        query_params (ImageQueryParams): Query parameters for image resizing.

    Returns:
        StreamingResponse: A StreamingResponse containing the downloaded image.

    Raises:
        exceptions.UnknownValue: If no vehicle image with the specified ID is found.
    """
    if vehicle_image is not None:
        file_bytes = download_file(VEHICLE_IMAGES, str(vehicle_image.id))
        if query_params.width is not None or query_params.height is not None:
            file_bytes = resize_image(
                file_bytes,
                width=query_params.width,
                height=query_params.height,
            )

        return StreamingResponse(
            BytesIO(file_bytes),
            media_type=vehicle_image.file_type,
            headers={
                "Content-Disposition": f'inline; filename="{vehicle_image.file_name}"',
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
    raise exceptions.UnknownValue(VehicleImage.id)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_VEHICLE_PICTURE,
    tags=["Vehicle Image"],
    response_model=VehicleImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidImageFile(),
            exceptions.UnknownValue(VehicleImage.vehicle_id),
            exceptions.UnknownValue(VehicleImage.company_id),
            exceptions.InvalidAssociation(
                VehicleImage.vehicle_id, VehicleImage.company_id
            ),
        ]
    ),
    description=(
        """
            **Uploads a vehicle image.**    
            - Executive must have a valid access token.    
            - Logged-in executive must have `company.vehicle.update` permission to upload vehicle images.    
        """
    ),
)
async def upload_vehicle_image_executive(
    form_param: CreateFormForEX = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE)

        validate_id(session, Company, form_param.company_id, VehicleImage.company_id)
        vehicle = validate_id(
            session, Vehicle, form_param.vehicle_id, VehicleImage.vehicle_id
        )
        if vehicle.company_id != form_param.company_id:
            raise exceptions.InvalidAssociation(
                VehicleImage.vehicle_id, VehicleImage.company_id
            )

        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        vehicle_image_data = create_image(
            session, CreateForm(**form_param.model_dump()), file_bytes
        )
        log_event(token, request_info, vehicle_image_data)
        return vehicle_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.delete(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    tags=["Vehicle Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vehicle image.**    
            - Executive must have a valid access token.       
            - To delete a vehicle image, the `company.vehicle.update` permission is required.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_vehicle_image_executive(
    id: int,
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)
        roles = get_executive_roles(session, token)
        verify_permission(roles, ExecutivePermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle_image = (
            session.query(VehicleImage).filter(VehicleImage.id == id).first()
        )
        if vehicle_image is not None:
            vehicle_image_data = delete_image(session, vehicle_image)
            log_event(token, request_info, vehicle_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_executive.get(
    URL_VEHICLE_PICTURE,
    tags=["Vehicle Image"],
    response_model=List[VehicleImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicle images.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def fetch_vehicle_image_executive(
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
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    tags=["Vehicle Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(VehicleImage.id)]
    ),
    description=(
        """
            **Download vehicle image in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_vehicle_image_executive(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(oauth2_executive),
):
    try:
        session = SessionLocal()
        verify_token(session, ExecutiveToken, access_token)

        vehicle_image = (
            session.query(VehicleImage).filter(VehicleImage.id == id).first()
        )
        return download_image(vehicle_image, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


# ---------------------------------------------------------------------------
## API endpoints [Operator]
# ---------------------------------------------------------------------------
@route_operator.post(
    URL_VEHICLE_PICTURE,
    tags=["Vehicle Image"],
    response_model=VehicleImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidImageFile(),
            exceptions.UnknownValue(VehicleImage.vehicle_id),
        ]
    ),
    description=(
        """
            **Uploads a vehicle image.**    
            - Operator must have a valid access token.    
            - Logged-in operator must have `company.vehicle.update` permission to upload vehicle images.    
        """
    ),
)
async def upload_vehicle_image_operator(
    form_param: CreateFormForOP = Depends(),
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_VEHICLE)

        validate_id(
            session,
            Vehicle,
            form_param.vehicle_id,
            VehicleImage.vehicle_id,
            extra_filter=Vehicle.company_id == token.company_id,
        )
        file_bytes = await form_param.file.read()
        validate_image(file_bytes, form_param.file.filename)

        vehicle_image_data = create_image(
            session,
            CreateForm(**form_param.model_dump(), company_id=token.company_id),
            file_bytes,
        )
        log_event(token, request_info, vehicle_image_data)
        return vehicle_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.delete(
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    tags=["Vehicle Image"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.NoPermission()]
    ),
    description=(
        """
            **Deletes a vehicle image.**    
            - Operator must have a valid access token.    
            - The logged-in operator must have the `company.vehicle.update` permission.    
            - Returns 204 No Content even if the specified image does not exist.    
        """
    ),
)
async def delete_vehicle_image_operator(
    id: int,
    access_token=Depends(bearer_operator),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)
        roles = get_operator_roles(session, token)
        verify_permission(roles, OperatorPermissionPath.UPDATE_COMPANY_VEHICLE)

        vehicle_image = (
            session.query(VehicleImage)
            .filter(
                VehicleImage.id == id, VehicleImage.company_id == token.company_id
            )
            .first()
        )
        if vehicle_image is not None:
            vehicle_image_data = delete_image(session, vehicle_image)
            log_event(token, request_info, vehicle_image_data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()


@route_operator.get(
    URL_VEHICLE_PICTURE,
    tags=["Vehicle Image"],
    response_model=List[VehicleImageSchema],
    responses=fuse_exception_responses([exceptions.InvalidToken()]),
    description=(
        """
            **Fetches a list of vehicle images.**    
            - Requires a valid access token for authentication.    
            - Only vehicle images belonging to the same company as the logged-in operator will be returned.    
        """
    ),
)
async def fetch_vehicle_image_operator(
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
    f"{URL_VEHICLE_PICTURE}/{{id}}",
    tags=["Vehicle Image"],
    responses=fuse_exception_responses(
        [exceptions.InvalidToken(), exceptions.UnknownValue(VehicleImage.id)]
    ),
    description=(
        """
            **Download vehicle image in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_vehicle_image_operator(
    id: int,
    query_params: ImageQueryParams = Depends(),
    access_token=Depends(bearer_operator),
):
    try:
        session = SessionLocal()
        token = verify_token(session, OperatorToken, access_token.credentials)

        vehicle_image = (
            session.query(VehicleImage)
            .filter(
                VehicleImage.id == id, VehicleImage.company_id == token.company_id
            )
            .first()
        )
        return download_image(vehicle_image, query_params)
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
