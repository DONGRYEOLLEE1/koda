"""Task model."""
from dataclasses import asdict, dataclass


@dataclass
class Task:
    id: int
    title: str
    done: bool = False

    def __post_init__(self):
        if not self.title.strip():
            raise ValueError("title must not be empty")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(id=int(data["id"]), title=data["title"], done=bool(data.get("done", False)))
