"""
Shared object key prefixes for MinIO storage.

This module centralizes object key prefixes used with the common
`MINIO_BUCKET` to keep storage paths consistent across all APIs.

Pattern:
    <prefix>/<primary_key>

Example:
    vehicle-images/123
"""

PREFIX_FOR_BUS_IMAGES = "bus-images"
PREFIX_FOR_EXECUTIVE_IMAGES = "executive-images"
PREFIX_FOR_OPERATOR_IMAGES = "operator-images"
PREFIX_FOR_VENDOR_IMAGES = "vendor-images"
PREFIX_FOR_COMPANY_IMAGES = "company-images"
PREFIX_FOR_BUSINESS_IMAGES = "business-images"
PREFIX_FOR_VEHICLE_IMAGES = "vehicle-images"
