"""
This module provides helper functions commonly used across FastAPI routes.

It offers reusable utilities that make it easier for developers to integrate them into their projects.
"""

from enum import Enum
from typing import Any, List, Dict, Type, Union, Tuple
from fastapi import Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import Column, asc, desc, event
from sqlalchemy.orm.session import Session
from sqlalchemy.orm.attributes import get_history

from app.src import argon2
from app.src import schemas, exceptions
from app.src.db import (
    ExecutiveRole,
    ExecutiveRoleMap,
    ExecutiveToken,
    Executive,
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


def orm_to_json(
    orm_object: Any,
    exclude: List[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Convert a SQLAlchemy model object into JSON-compatible dicts.

    Args:
        orm_object (Any): SQLAlchemy model instance
        exclude (List[str]): list of fields to exclude, defaults to None

    Returns:
        Tuple[Dict[str, Any], Dict[str, Any]]:
            - data: the full JSON data.
            - stripped: same as full data but with sensitive fields removed.
    """
    exclude = set(exclude or [])
    data = jsonable_encoder(orm_object)
    stripped = {}
    for key, value in data.items():
        if key not in exclude:
            stripped[key] = value
    return data, stripped


@event.listens_for(Executive, "before_insert")
@event.listens_for(Executive, "before_update")
def preprocess_password(
    mapper,
    connection,
    model_cls: Type[Union[ExecutiveToken, OperatorToken, VendorToken]],
) -> None:
    """
    Hash password before insert or update.
    """
    history = get_history(model_cls, "password")

    if history.added:
        model_cls.password = argon2.make_password(model_cls.password)
