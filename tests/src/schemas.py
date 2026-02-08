"""
Pydantic schemas used across the EnteBus Tests.

These models define the structure of data that is reused across multiple test endpoints.
"""

from app.api.executive_token import ExecutiveTokenSchema


class TokenHolder(ExecutiveTokenSchema):
    def HEADER(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}
