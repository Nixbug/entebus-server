from enum import Enum
from typing import TypeVar
from pydantic import BaseModel
from shapely.geometry.base import BaseGeometry

from app.src.db import ExecutiveToken
from app.src.db import ORMbase
from app.src.db import OperatorToken
from app.src.db import VendorToken

# ---------------------------------------------------------------------------
## Type Variables
# ---------------------------------------------------------------------------
TokenT = TypeVar("TokenT", ExecutiveToken, OperatorToken, VendorToken)
BaseModelT = TypeVar("BaseModelT", bound=BaseModel)
ORMbaseT = TypeVar("ORMbaseT", bound=ORMbase)
GeometryT = TypeVar("GeometryT", bound=BaseGeometry)
EnumT = TypeVar("EnumT", bound=Enum)
