from typing import BinaryIO, Optional
from minio import Minio
from minio.error import S3Error

from app.src.constants import MINIO_HOST, MINIO_PASSWORD, MINIO_PORT, MINIO_USERNAME

# MinIO client instance
client: Minio = Minio(
    endpoint=f"{MINIO_HOST}:{MINIO_PORT}",
    access_key=MINIO_USERNAME,
    secret_key=MINIO_PASSWORD,
    secure=False,
)


def create_bucket(bucket_name: str) -> None:
    """
    Create a new bucket in MinIO if it does not already exist.

    Args:
        bucket_name (str): The name of the bucket to create.

    Raises:
        S3Error: If the bucket cannot be created.
    """
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)


def delete_bucket(bucket_name: str) -> None:
    """
    Delete a bucket and all its contents from MinIO if it exists.

    Args:
        bucket_name (str): The name of the bucket to delete.

    Note:
        This will remove all objects inside the bucket before deleting it.

    Raises:
        S3Error: If the bucket or objects cannot be deleted.
    """
    if client.bucket_exists(bucket_name):
        objects_in_bucket = client.list_objects(bucket_name)
        for obj in objects_in_bucket:
            client.remove_object(bucket_name, obj.object_name)
        client.remove_bucket(bucket_name)


def download_file(bucket_name: str, object_id: str) -> Optional[bytes]:
    """
    Download a file from MinIO.

    Args:
        bucket_name (str): The name of the bucket containing the object.
        object_id (str): The unique identifier (key) of the object.

    Returns:
        Optional[bytes]: The raw file data if found, otherwise None.

    Raises:
        S3Error: For MinIO errors other than file-not-found.
    """
    try:
        response = client.get_object(bucket_name, object_id)
        return response.data
    except S3Error as err:
        if err.code in ("NoSuchKey", "NoSuchObject"):
            return None
        raise


def delete_file(bucket_name: str, object_id: str) -> bool:
    """
    Delete a file from MinIO. If the file is not found, do nothing.

    Args:
        bucket_name (str): The name of the bucket containing the object.
        object_id (str): The unique identifier (key) of the object.

    Raises:
        S3Error: If a non-404 error occurs during deletion.
    """
    try:
        client.remove_object(bucket_name, object_id)
        return True
    except S3Error as err:
        if err.code == "NoSuchKey":
            return False
        raise


def upload_file(
    bucket_name: str,
    object_id: str,
    size: int,
    file_object: BinaryIO,
) -> None:
    """
    Upload a file to MinIO.

    Args:
        bucket_name (str): The name of the bucket where the file will be stored.
        object_id (str): The unique identifier (key) for the object in MinIO.
        size (int): The size of the file in bytes.
        file_object (BinaryIO): A file-like object containing the data to upload.

    Raises:
        S3Error: If the file cannot be uploaded.
    """
    client.put_object(bucket_name, object_id, file_object, size)
