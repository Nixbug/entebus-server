from typing import TypeVar
from pydantic import BaseModel
from shapely.geometry.base import BaseGeometry

from app.src.db import ExecutiveToken, ORMbase, OperatorToken, VendorToken

# ---------------------------------------------------------------------------
## Type Variables
# ---------------------------------------------------------------------------
TokenT = TypeVar("TokenT", ExecutiveToken, OperatorToken, VendorToken)
BaseModelT = TypeVar("BaseModelT", bound=BaseModel)
ORMbaseT = TypeVar("ORMbaseT", bound=ORMbase)
GeometryT = TypeVar("GeometryT", bound=BaseGeometry)
