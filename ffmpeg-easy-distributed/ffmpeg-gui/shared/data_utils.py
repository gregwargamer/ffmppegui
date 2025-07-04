import logging
from typing import Type, TypeVar, Any, Dict
from enum import Enum
import inspect

T = TypeVar('T')
logger = logging.getLogger(__name__)

def safe_dataclass_from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
    """
    Safely creates a dataclass instance from a dictionary.

    - Ignores extraneous keys in the data dictionary.
    - Fills in default values for missing keys in the dictionary.
    - Safely converts string values to Enum members.
    - Logs warnings for any discrepancies.
    """
    if not data:
        logger.warning(f"Received empty or None data for dataclass {cls.__name__}. Returning default instance.")
        return cls()

    kwargs = {}

    for name, field_type in cls.__annotations__.items():
        if name not in data:
            # If data is missing a key, the dataclass will use its default value, so we don't need to do anything.
            # We could log a warning if there's no default value, but dataclasses handle that.
            continue

        value = data.get(name)
        
        # Handle Enum conversion
        if inspect.isclass(field_type) and issubclass(field_type, Enum):
            try:
                kwargs[name] = field_type(value)
            except ValueError:
                logger.warning(f"Invalid enum value '{value}' for field '{name}' of type '{field_type.__name__}'. Using default.")
                # Let dataclass handle default value by not including this key
        
        # Handle nested dataclass conversion
        elif hasattr(field_type, '__annotations__'): # Check if it's likely a dataclass
            if isinstance(value, dict):
                kwargs[name] = safe_dataclass_from_dict(field_type, value)
            else:
                logger.warning(f"Expected a dict for nested dataclass field '{name}' but got {type(value)}. Skipping.")

        else:
            kwargs[name] = value

    # Check for extraneous data
    extra_keys = set(data.keys()) - set(cls.__annotations__.keys())
    if extra_keys:
        logger.debug(f"Extraneous keys in data for {cls.__name__}: {extra_keys}")

    try:
        return cls(**kwargs)
    except TypeError as e:
        logger.error(f"TypeError creating {cls.__name__}: {e}. Kwargs used: {kwargs}. Original data: {data}")
        # Return a default instance to prevent crashing
        return cls()
