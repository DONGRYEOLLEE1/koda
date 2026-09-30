"""Response models."""
from dataclasses import dataclass


@dataclass
class Current:
    city: str
    temp_c: float
    condition: str

    @classmethod
    def from_json(cls, data: dict) -> "Current":
        return cls(city=data["city"], temp_c=float(data["temp_c"]), condition=data["condition"])
