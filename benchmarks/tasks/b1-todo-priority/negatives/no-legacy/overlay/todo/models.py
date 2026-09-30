"""Task model."""
from dataclasses import asdict, dataclass

PRIORITIES = ("low", "normal", "high")
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


@dataclass
class Task:
    id: int
    title: str
    done: bool = False
    priority: str = "normal"

    def __post_init__(self):
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if self.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}, got {self.priority!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(id=int(data["id"]), title=data["title"], done=bool(data.get("done", False)),
                   priority=data["priority"])
