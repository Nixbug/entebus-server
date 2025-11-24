from fastapi import APIRouter, Depends, status, Form, UploadFile, File
from pydantic import BaseModel, Field
from io import BytesIO
from datetime import datetime
from PIL import Image

from app.src.buckets import EXECUTIVE_IMAGES
from app.src import exceptions
from app.src.urls import URL_EXECUTIVE_PICTURE
from app.src.minio import upload_file
from app.api.bearer import oauth2_executive
from app.src.db import ExecutiveToken, ExecutiveImage, SessionLocal
from app.src.permissions.executive import PermissionPath
from app.src.openobserve import log_event
from app.src.validators import verify_permission, verify_token
from app.src.functions import (
    fuse_exception_responses,
    get_request_info,
    get_executive_roles,
    orm_to_json,
    split_MIME,
)

route_executive = APIRouter()


## Output Schema
class ExecutiveImageSchema(BaseModel):
    id: int
    executive_id: int
    file_name: str
    file_type: str
    file_size: int
    created_on: datetime


## Input Forms
class createForm(BaseModel):
    executive_id: int | None = Field(Form(default=None))
    file: UploadFile = Field(File())


# ---------------------------------------------------------------------------
## API endpoints [Executive]
# ---------------------------------------------------------------------------
@route_executive.post(
    URL_EXECUTIVE_PICTURE,
    tags=["Account Image"],
    response_model=ExecutiveImageSchema,
    status_code=status.HTTP_201_CREATED,
    responses=fuse_exception_responses(
        [
            exceptions.InvalidToken(),
            exceptions.NoPermission(),
            exceptions.InvalidCredentials(),
        ]
    ),
    description="""
    Uploads an executive image.
    """,
)
async def upload_executive_image(
    fParam: createForm = Depends(),
    access_token=Depends(oauth2_executive),
    request_info=Depends(get_request_info),
):
    try:
        session = SessionLocal()
        token = verify_token(session, ExecutiveToken, access_token)

        if fParam.executive_id is None:
            fParam.executive_id = token.executive_id
        is_self_update = fParam.executive_id == token.executive_id
        if not is_self_update:
            roles = get_executive_roles(session, token)
            verify_permission(roles, PermissionPath.CREATE_EXECUTIVE)
        file_bytes = await fParam.file.read()
        mime_info = split_MIME(fParam.file.content_type)
        mime_type = mime_info["type"]
        if mime_type != "image":
            raise exceptions.InvalidImage("File is not an image")

        image = Image.open(BytesIO(file_bytes))
        width, height = image.size
        if image:
            image.verify()
        else:
            raise exceptions.InvalidImage("Invalid or corrupted image file.")

        # Allowed extensions/formats
        ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
        if image.format not in ALLOWED_FORMATS:
            raise exceptions.InvalidImage(f"Unsupported image format: {image.format}")

        # Resolution validation
        MAX_WIDTH = 4000
        MAX_HEIGHT = 4000
        MIN_WIDTH = 100
        MIN_HEIGHT = 100

        if not (MIN_WIDTH <= width <= MAX_WIDTH):
            raise exceptions.InvalidImage("Invalid image width resolution.")

        if not (MIN_HEIGHT <= height <= MAX_HEIGHT):
            raise exceptions.InvalidImage("Invalid image height resolution.")

        executiveI_image = ExecutiveImage(
            executive_id=fParam.executive_id,
            file_name=fParam.file.filename,
            file_type=fParam.file.content_type,
            file_size=len(file_bytes),
        )
        session.add(executiveI_image)
        session.commit()
        session.refresh(executiveI_image)
        upload_file(
            EXECUTIVE_IMAGES,
            str(executiveI_image.id),
            len(file_bytes),
            BytesIO(file_bytes),
        )

        _, executive_image_data = orm_to_json(executiveI_image)
        log_event(token, request_info, executive_image_data)
        return executive_image_data
    except Exception as e:
        exceptions.handle(e)
    finally:
        session.close()
