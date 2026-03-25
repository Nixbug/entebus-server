"""
This module provides helper functions commonly used across FastAPI routes.

It offers reusable utilities that make it easier for developers to integrate them into their projects.
"""

import mimetypes
import pyproj
from enum import Enum
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from typing import Any, List, Dict, Type, Union
from fastapi import Query, Request
from pydantic import BaseModel
from sqlalchemy import Column, asc, desc
from sqlalchemy.orm.session import Session
from shapely.geometry.base import BaseGeometry
from shapely import Polygon, wkt, errors
from shapely.ops import transform

from app.src import schemas, exceptions
from app.src.constants import (
    MAX_IMAGE_FILE_SIZE,
    MAX_IMAGE_RESOLUTION,
    MIN_IMAGE_FILE_SIZE,
    MIN_IMAGE_RESOLUTION,
)
from app.src.db import (
    ExecutiveRole,
    ExecutiveRoleMap,
    ExecutiveToken,
    ORMbase,
    OperatorRole,
    OperatorRoleMap,
    OperatorToken,
    VendorRole,
    VendorRoleMap,
    VendorToken,
)


def get_request_info(request: Request) -> schemas.RequestInfo:
    """
    Extract metadata about the incoming request.

    This function retrieves essential request information — HTTP method,
    path, and associated application ID — and returns it as a
    `RequestInfo` Pydantic model for structured use across the system.

    Args:
        request (Request): FastAPI request object.

    Returns:
        schemas.RequestInfo: Pydantic model containing:
            - method (str): HTTP method (GET, POST, etc.).
            - path (str): Path portion of the request URL.
            - app_id (int): Application ID from app state.
    """
    return schemas.RequestInfo(
        method=request.method,
        path=request.url.path,
        app_id=request.scope["app"].state.id,
    )


def fuse_exception_responses(
    exceptions: List[exceptions.APIException],
) -> Dict[int, dict]:
    """
    Generate OpenAPI response documentation by fusing multiple APIException instances.

    Args:
        exceptions (List[exceptions.APIException]): List of instantiated exceptions.

    Returns:
        Dict[int, dict]: A dictionary of OpenAPI response specs grouped by status code.
    """
    responses = {}

    for exception in exceptions:
        status_code = exception.status_code
        example_key = type(exception).__name__
        example_value = {
            "summary": str(exception.headers),
            "value": {"detail": exception.detail},
        }

        if status_code not in responses:
            responses[status_code] = {
                "model": schemas.ErrorResponse,
                "content": {
                    "application/json": {"examples": {example_key: example_value}}
                },
            }
        else:
            responses[status_code]["content"]["application/json"]["examples"][
                example_key
            ] = example_value

    return responses


def enum_str(enum_class: Type[Enum]) -> str:
    """
    Convert an Enum class into a comma-separated string of its members.

    Each enum member is formatted as "<NAME>: <VALUE>".

    Args:
        enumClass (Type[Enum]): The Enum class to be stringified.

    Returns:
        str: A human-readable string representation of the enum members.
    """
    return ", ".join(f"{x.name}: {x.value}" for x in enum_class)


def cleanup_old_tokens(
    session: Session,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
    filter_condition: Column,
    max_tokens: int,
) -> None:
    """
    Remove excess tokens for a given entity, retaining only the most recent valid ones.

    This function enforces a maximum number of active tokens per entity (executive,
    operator, vendor.) and deletes older ones beyond the specified limit.
    Tokens are ordered such that revoked tokens are prioritized for deletion.

    Args:
        session (Session): Active SQLAlchemy session.
        model_cls Type[Union[ExecutiveToken, OperatorToken, VendorToken]]: The ORM model class.
        filter_condition (Column): SQLAlchemy filter condition.
        max_tokens (int): The maximum number of tokens allowed.

    Returns:
        None
    """
    tokens = (
        session.query(model_cls)
        .filter(filter_condition)
        .order_by(desc(model_cls.is_revoked), asc(model_cls.created_on))
        .all()
    )
    # Remove oldest tokens if we exceed max_tokens
    while len(tokens) > max_tokens:
        token_to_delete = tokens.pop(0)
        session.delete(token_to_delete)
        session.flush()


def get_executive_roles(
    session: Session,
    token: ExecutiveToken,
) -> list[ExecutiveRole]:
    """
    Retrieve all roles assigned to a specific executive.

    Args:
        session (Session): Active SQLAlchemy session.
        token (ExecutiveToken): Token model instance.

    Returns:
        list[ExecutiveRole]: List of ExecutiveRole objects assigned to the executive.
                             Returns an empty list if no roles are found.
    """
    return (
        session.query(ExecutiveRole)
        .join(ExecutiveRoleMap, ExecutiveRole.id == ExecutiveRoleMap.role_id)
        .filter(ExecutiveRoleMap.executive_id == token.executive_id)
        .all()
    )


def get_vendor_roles(
    session: Session,
    token: VendorToken,
) -> list[VendorRole]:
    """
    Retrieve all roles assigned to a specific vendor.

    Args:
        session (Session): Active SQLAlchemy session.
        token (VendorToken): Token model instance.

    Returns:
        list[VendorRole]: List of VendorRole objects assigned to the vendor.
                          Returns an empty list if no roles are found.
    """
    return (
        session.query(VendorRole)
        .join(VendorRoleMap, VendorRole.id == VendorRoleMap.role_id)
        .filter(VendorRoleMap.vendor_id == token.vendor_id)
        .all()
    )


def get_operator_roles(
    session: Session,
    token: OperatorToken,
) -> list[OperatorRole]:
    """
    Retrieve all roles assigned to a specific operator.

    Args:
        session (Session): Active SQLAlchemy session.
        token (OperatorToken): Token model instance.

    Returns:
        list[OperatorRole]: List of OperatorRole objects assigned to the operator.
                            Returns an empty list if no roles are found.
    """
    return (
        session.query(OperatorRole)
        .join(OperatorRoleMap, OperatorRole.id == OperatorRoleMap.role_id)
        .filter(OperatorRoleMap.operator_id == token.operator_id)
        .all()
    )


def get_by_path(data: dict, path: str) -> Any:
    """
    Retrieve a nested value from a dictionary using a dot-separated key path.

    Args:
        data (dict): The dictionary to traverse.
        path (str): Dot-separated string representing the path.

    Returns:
        Any: The value at the specified path.
    """
    for key in path.split("."):
        data = data[key]
    return data


def apply_id_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply ID-based filters to a SQLAlchemy query.

    This function adds filters based on ID equality, range, or a list of IDs.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.id is not None:
        query = query.filter(model_cls.id == params.id)
    if params.id_ge is not None:
        query = query.filter(model_cls.id >= params.id_ge)
    if params.id_le is not None:
        query = query.filter(model_cls.id <= params.id_le)
    if params.id_list is not None:
        query = query.filter(model_cls.id.in_(params.id_list))
    return query


def apply_created_on_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply creation date filters to a SQLAlchemy query.

    This function filters records based on their created_on timestamp.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.created_on_ge is not None:
        query = query.filter(model_cls.created_on >= params.created_on_ge)
    if params.created_on_le is not None:
        query = query.filter(model_cls.created_on <= params.created_on_le)
    return query


def apply_updated_on_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply update date filters to a SQLAlchemy query.

    This function filters records based on their updated_on timestamp.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.
    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.updated_on_ge is not None:
        query = query.filter(model_cls.updated_on >= params.updated_on_ge)
    if params.updated_on_le is not None:
        query = query.filter(model_cls.updated_on <= params.updated_on_le)
    return query


def apply_client_data_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply client data filters to a SQLAlchemy query.

    This function filters records based on platform_type, list of platform_type and client_details.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.platform_type is not None:
        query = query.filter(model_cls.platform_type == params.platform_type)
    if params.platform_type_list is not None:
        query = query.filter(model_cls.platform_type.in_(params.platform_type_list))
    if params.client_details is not None:
        query = query.filter(
            model_cls.client_details.ilike(f"%{params.client_details}%")
        )
    return query


def apply_name_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply name filters to a SQLAlchemy query.

    This function filters records based on name.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.name is not None:
        query = query.filter(model_cls.name.ilike(f"%{params.name}%"))
    return query


def apply_account_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply account filters to a SQLAlchemy query.

    This function filters records based on username, gender, full_name, email_id, and phone_number.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.username is not None:
        query = query.filter(model_cls.username.ilike(f"%{params.username}%"))
    if params.gender is not None:
        query = query.filter(model_cls.gender == params.gender)
    if params.full_name is not None:
        query = query.filter(model_cls.full_name.ilike(f"%{params.full_name}%"))
    if params.email_id is not None:
        query = query.filter(model_cls.email_id.ilike(f"%{params.email_id}%"))
    if params.phone_number is not None:
        query = query.filter(model_cls.phone_number.ilike(f"%{params.phone_number}%"))
    return query


def apply_status_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply status-based filters to a SQLAlchemy query.

    This function filters records based on status.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.status_list is not None:
        query = query.filter(model_cls.status.in_(params.status_list))
    return query


def apply_type_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply type-based filters to a SQLAlchemy query.

    This function filters records based on type.
    The filters are applied only if the corresponding parameter values are provided.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.type_list is not None:
        query = query.filter(model_cls.type.in_(params.type_list))
    return query


def apply_picture_filters(
    query: Query, model_cls: Type[ORMbase], params: BaseModel
) -> Query:
    """
    Apply file metadata filters to a SQLAlchemy query.

    This function filters records based on file_name, file_type, file_size_ge, and file_size_le.

    Args:
        query (Query): Active SQLAlchemy query object.
        model_cls (Type[ORMbase]): SQLAlchemy model class containing the relevant column.
        params (BaseModel): Pydantic model instance.

    Returns:
        Query: Updated SQLAlchemy query with applied filters.
    """
    if params.file_name is not None:
        query = query.filter(model_cls.file_name.ilike(f"%{params.file_name}%"))
    if params.file_type is not None:
        query = query.filter(model_cls.file_type.ilike(f"%{params.file_type}%"))
    if params.file_size_ge is not None:
        query = query.filter(model_cls.file_size >= params.file_size_ge)
    if params.file_size_le is not None:
        query = query.filter(model_cls.file_size <= params.file_size_le)
    return query


def update_if_changed(target_obj: Any, source_obj: dict) -> None:
    """
    Update attributes on a target object based on values from a source object.

    Args:
        target_obj (Any): The model instance to be updated.
        source_obj (dict): A dictionary containing new values.

    Returns:
        None
    """
    for field, new_value in source_obj.items():
        old_value = getattr(target_obj, field, None)
        if new_value != old_value:
            setattr(target_obj, field, new_value)


# Set decompression bomb guard
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_RESOLUTION * MAX_IMAGE_RESOLUTION


def validate_image(file_bytes: bytes, filename: str) -> None:
    """
    Validate an image file based on its content type and size.

    Args:
        file_bytes (bytes): The bytes of the image file.
        filename (str): The filename of the image file.

    Raises:
        InvalidImageFile: If the image file is invalid.
    """
    try:
        guessed_mime, _ = mimetypes.guess_type(filename)
        if not guessed_mime or not guessed_mime.startswith("image/"):
            raise exceptions.InvalidImageFile()

        size = len(file_bytes)
        if size > MAX_IMAGE_FILE_SIZE or size < MIN_IMAGE_FILE_SIZE:
            raise exceptions.InvalidImageFile()

        with Image.open(BytesIO(file_bytes)) as image:
            image.load()
            width, height = image.size
            if not (MIN_IMAGE_RESOLUTION <= width <= MAX_IMAGE_RESOLUTION) or not (
                MIN_IMAGE_RESOLUTION <= height <= MAX_IMAGE_RESOLUTION
            ):
                raise exceptions.InvalidImageFile()
    except Image.DecompressionBombError:
        raise exceptions.InvalidImageFile()
    except UnidentifiedImageError:
        raise exceptions.InvalidImageFile()


def resize_image(file_bytes: bytes, width: int = None, height: int = None) -> bytes:
    """
    Resize an image file to fit within the specified width and height while maintaining aspect ratio.

    Uses PIL's thumbnail method, which scales the image to fit within the given dimensions
    without distorting the aspect ratio. The resulting image may be smaller than the requested
    width and height in one or both dimensions, depending on the original aspect ratio.

    Args:
        file_bytes (bytes): The bytes of the image file.
        width (int): The width for the resized image, defaults to None.
        height (int): The height for the resized image, defaults to None.

    Returns:
        bytes: The resized image file as bytes.
    """
    image = Image.open(BytesIO(file_bytes))
    if width or height:
        image.thumbnail((width or image.width, height or image.height))

    buffer = BytesIO()
    image.save(buffer, image.format)
    return buffer.getvalue()


def validate_srid_4326(geometry: BaseGeometry) -> bool:
    """
    Validate that a Shapely geometry contains WGS84 (SRID 4326) compatible coordinates.

    This function checks if all coordinates within the geometry fall within the
    valid WGS84 lon/lat ranges. The validation supports both singular and composite geometries and inspects:
        - Exterior coordinates for polygons
        - Direct coordinates for simple geometries
        - Coordinates of each geometry in multi-geometries (recursively)

    Args:
        geometry (BaseGeometry): Shapely geometry instance.

    Returns:
        bool: True if all coordinates fall within valid WGS84 lon/lat ranges.

    Raises:
        InvalidSRID4326: If any coordinate lies outside SRID 4326 bounds.
    """

    def check_coords(coords):
        for longitude, latitude in coords:
            if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
                raise exceptions.InvalidSRID4326()
        return True

    # Check single geometries
    if hasattr(geometry, "exterior"):
        check_coords(geometry.exterior.coords)
    elif hasattr(geometry, "coords"):
        check_coords(geometry.coords)

    # Check Multi* geometries recursively
    if hasattr(geometry, "geoms"):
        for geom in geometry.geoms:
            validate_srid_4326(geom)

    return True


def validate_wkt_string(
    wkt_string: str, expected_type: Type[BaseGeometry]
) -> BaseGeometry:
    """
    Validate and parse a WKT string into a Shapely geometry of the expected type.

    Args:
        wkt_string (str): Well-Known Text (WKT) geometry string.
        expected_type (Type[BaseGeometry]): Expected Shapely geometry class.

    Returns:
        BaseGeometry: Parsed Shapely geometry instance.

    Raises:
        InvalidWKTStringOrType: If WKT parsing fails or type does not match `expected_type`.
    """
    try:
        geom = wkt.loads(wkt_string)
    except errors.ShapelyError:
        raise exceptions.InvalidWKTStringOrType()

    if not isinstance(geom, expected_type):
        raise exceptions.InvalidWKTStringOrType()

    return geom


def validate_AABB(geometry: BaseGeometry) -> bool:
    """
    Validate that the provided geometry is a valid Axis-Aligned Bounding Box (AABB).

    Args:
        geometry (BaseGeometry): Shapely geometry instance to validate.

    Returns:
        bool: True if the geometry is a valid AABB.

    Raises:
        InvalidAABB: If the geometry violates AABB structural or alignment rules.
    """
    if not isinstance(geometry, Polygon):
        raise exceptions.InvalidAABB()

    coords = list(geometry.exterior.coords)
    if len(coords) != 5:
        raise exceptions.InvalidAABB()

    rect = coords[:-1]  # Remove duplicate closing coordinate

    for i in range(4):
        x1, y1 = rect[i]
        x2, y2 = rect[(i + 1) % 4]
        if not (x1 == x2 or y1 == y2):
            raise exceptions.InvalidAABB()

    return True


def get_area(geom: BaseGeometry) -> float:
    """
    Calculate the area of a Shapely geometry in square meters.

    Args:
        geom (BaseGeometry): Shapely `Polygon` geometry in WGS84.

    Returns:
        float: Area of the geometry in square meters.
    """
    projection = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:6933", always_xy=True
    ).transform

    projected_geom = transform(projection, geom)
    return projected_geom.area


def resolve_model_defaults(model_cls: Type[BaseModel], **overrides):
    """
    Build a model instance with all Query() defaults resolved to concrete values.

    Args:
        model_cls (Type[BaseModel]): The Pydantic model class to build.
        **overrides: Field values to override the defaults.

    Returns:
        BaseModel: An instance of model_cls with all Query() defaults resolved.
    """
    data = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name in overrides:
            data[field_name] = overrides[field_name]
        else:
            default_val = field_info.default
            if hasattr(default_val, "default") and not isinstance(default_val, type):
                data[field_name] = default_val.default
            else:
                data[field_name] = default_val
    return model_cls(**data)
