"""
Shared object key prefixes for MinIO storage.

This module centralizes object key prefixes used with the common
`MINIO_BUCKET` to keep storage paths consistent across all APIs.

Pattern:
    <prefix>/<primary_key>

Example:
    vehicle-images/123
"""

PREFIX_BUS_IMAGES = "bus-images"
PREFIX_EXECUTIVE_IMAGES = "executive-images"
PREFIX_OPERATOR_IMAGES = "operator-images"
PREFIX_VENDOR_IMAGES = "vendor-images"
PREFIX_COMPANY_IMAGES = "company-images"
PREFIX_BUSINESS_IMAGES = "business-images"
PREFIX_VEHICLE_IMAGES = "vehicle-images"
