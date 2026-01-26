"""Pydantic v1/v2 compatibility utilities.

This module provides wrapper functions that work with both Pydantic v1 and v2,
allowing ELLE to run regardless of which version is installed.

Usage:
    from elle.common.pydantic_compat import (
        safe_model_dump, safe_model_dump_json, safe_model_validate
    )

    # Instead of obj.model_dump()
    data = safe_model_dump(obj)

    # Instead of obj.model_dump_json()
    json_str = safe_model_dump_json(obj)

    # Instead of cls.model_validate(data)
    instance = safe_model_validate(MyModel, data)
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from pydantic import BaseModel

T = TypeVar("T", bound="BaseModel")


def safe_model_dump(obj: BaseModel, **kwargs: Any) -> dict[str, Any]:
    """Convert a Pydantic model to a dictionary (v1/v2 compatible).

    Args:
        obj: The Pydantic model instance to convert.
        **kwargs: Additional arguments passed to the underlying method.
            Common kwargs: exclude_none, exclude_unset, by_alias

    Returns:
        Dictionary representation of the model.
    """
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump(**kwargs)
    # Pydantic v1 fallback
    return obj.dict(**kwargs)


def safe_model_dump_json(obj: BaseModel, **kwargs: Any) -> str:
    """Convert a Pydantic model to a JSON string (v1/v2 compatible).

    Args:
        obj: The Pydantic model instance to convert.
        **kwargs: Additional arguments passed to the underlying method.

    Returns:
        JSON string representation of the model.
    """
    # Pydantic v2
    if hasattr(obj, "model_dump_json"):
        return obj.model_dump_json(**kwargs)
    # Pydantic v1 fallback
    return json.dumps(obj.dict(**kwargs))


def safe_model_validate(cls: type[T], data: Any) -> T:
    """Create a Pydantic model instance from data (v1/v2 compatible).

    Args:
        cls: The Pydantic model class.
        data: Dictionary or other data to validate.

    Returns:
        Validated model instance.
    """
    # Pydantic v2
    if hasattr(cls, "model_validate"):
        return cls.model_validate(data)
    # Pydantic v1 fallback
    return cls.parse_obj(data)


def safe_model_json_schema(cls: type[BaseModel], **kwargs: Any) -> dict[str, Any]:
    """Get JSON schema for a Pydantic model (v1/v2 compatible).

    Args:
        cls: The Pydantic model class.
        **kwargs: Additional arguments passed to the underlying method.

    Returns:
        JSON schema dictionary.
    """
    # Pydantic v2
    if hasattr(cls, "model_json_schema"):
        return cls.model_json_schema(**kwargs)
    # Pydantic v1 fallback
    return cls.schema(**kwargs)
