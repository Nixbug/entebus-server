"""
MinIO Bucket Names

These constants define the bucket names used in MinIO for storing
different types of uploaded pictures. Keeping them centralized
ensures consistency across the codebase and avoids hardcoding
strings in multiple places.
"""

BUS_IMAGES = "bus-images"  # Stores pictures of the bus
EXECUTIVE_IMAGES = "executive-images"  # Stores profile pictures of executives
OPERATOR_IMAGES = "operator-images"  # Stores profile pictures of operators
VENDOR_IMAGES = "vendor-images"  # Stores profile pictures of vendors
COMPANY_IMAGES = "company-images"  # Stores the logo of the company
BUSINESS_IMAGES = "business-images"  # Stores the logo of the business

ALL = [
    BUS_IMAGES,
    BUSINESS_IMAGES,
    COMPANY_IMAGES,
    EXECUTIVE_IMAGES,
    OPERATOR_IMAGES,
    VENDOR_IMAGES,
]
