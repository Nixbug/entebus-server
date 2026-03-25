"""
Operator Image API Router for EnteBus.

Provides endpoints for managing operator images, including retrieval.
Uses Pydantic schemas for input validation and structured output.
Endpoints for creation, and deletion are planned for future implementation.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from io import BytesIO
from typing import List
from enum import StrEnum
from pydantic import BaseModel, Field
from sqlalchemy.orm.session import Session

from app.src.buckets import OPERATOR_IMAGES
from app.src.constants import MIN_IMAGE_RESOLUTION, MAX_IMAGE_RESOLUTION
from app.api.bearer import oauth2_executive, bearer_operator
from app.src import exceptions
from app.src.enums import OrderIn
from app.src.filters import (
    CreatedOnFilter,
    IDFilter,
    PaginationFilter,
    PictureFilter,
)
from app.src.db import SessionLocal, ExecutiveToken, OperatorToken, OperatorImage
from app.src.minio import download_file
from app.src.validators import verify_token
from app.src.functions import (
    apply_created_on_filters,
    apply_id_filters,
    apply_picture_filters,
    enum_str,
    fuse_exception_responses,
    resize_image,
)
from app.src.urls import URL_OPERATOR_PICTURE

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

    width: int | None = Field(Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION))
    height: int | None = Field(Query(default=None, ge=MIN_IMAGE_RESOLUTION, le=MAX_IMAGE_RESOLUTION))


def search_image(
    session: Session, query_params: QueryParams
) -> list[OperatorImageSchema]:
    """
    Search for operator images based on provided query parameters.

    This function supports multiple filtering, searching, ordering, and
    pagination capabilities to retrieve operator images that match various criteria.

    Args:
        session (Session): SQLAlchemy database session.
        query_params (QueryParams): Query parameters containing search criteria.

    Returns:
        List[OperatorImageSchema]: List of OperatorImageSchema instances that match the search criteria.
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
    raise exceptions.UnknownValue(OperatorImage.id)


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
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
async def fetch_operator_image_executive(
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
            **Download executive profile picture in original or resized resolution.**    
            - Requires a valid access token for authentication.    
        """
    ),
)
async def download_operator_image_executive(
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
async def fetch_operator_image_operator(
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
async def download_operator_image_operator(
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
