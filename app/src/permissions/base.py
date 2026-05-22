from pydantic import BaseModel


class PermissionBase(BaseModel):
    @classmethod
    def all_granted(cls):
        return cls(
            **{
                name: (
                    field.annotation.all_granted()  # recurse if nested model
                    if isinstance(field.annotation, type)
                    and issubclass(field.annotation, BaseModel)
                    else True  # leaf bool field
                )
                for name, field in cls.model_fields.items()
            }
        )

    @classmethod
    def all_denied(cls):
        return cls(
            **{
                name: (
                    field.annotation.all_denied()
                    if isinstance(field.annotation, type)
                    and issubclass(field.annotation, BaseModel)
                    else False
                )
                for name, field in cls.model_fields.items()
            }
        )
