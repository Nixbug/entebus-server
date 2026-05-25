"""
This module defines the `Description` class, which is used to build
multi-line descriptions for API endpoints in a structured way.
"""

class Description:
    def __init__(self):
        self.parts: list[str] = []

    def add_head(self, text: str):
        self.parts.append(f"\n{text}")
        return self

    def add_line(self, text: str):
        self.parts.append(f"- {text}")
        return self

    def copy(self):
        new = Description()
        new.parts = self.parts.copy()
        return new

    def to_string(self) -> str:
        return "\n".join(self.parts).strip()

    def __str__(self) -> str:
        return self.to_string()
