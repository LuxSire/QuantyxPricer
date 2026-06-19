import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class User:
    """Represents one user record."""

    id: int
    name: str
    email: str
    password: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        """Create a User instance from a dictionary."""
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")),
            email=str(data.get("email", "")),
            password=str(data.get("password", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert User to dictionary."""
        return asdict(self)


class Users:
    """Dictionary-like collection of User objects keyed by id."""

    def __init__(self, users_dict: Optional[Dict[int, User]] = None):
        self._users: Dict[int, User] = users_dict or {}

    @classmethod
    def from_list(cls, data: List[Dict[str, Any]]) -> "Users":
        """Create Users from a list of dictionaries."""
        users_dict: Dict[int, User] = {}
        for item in data:
            user = User.from_dict(item)
            users_dict[user.id] = user
        return cls(users_dict)

    @classmethod
    def from_json_file(cls, filepath: str) -> "Users":
        """Load Users from a JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return cls.from_list(data)

        if isinstance(data, dict):
            users_dict: Dict[int, User] = {}
            for key, item in data.items():
                if isinstance(item, User):
                    users_dict[int(key)] = item
                elif isinstance(item, dict):
                    user = User.from_dict(item)
                    users_dict[user.id] = user
            return cls(users_dict)

        return cls({})

    def __getitem__(self, user_id: int) -> User:
        return self._users[user_id]

    def __setitem__(self, user_id: int, user: User) -> None:
        self._users[user_id] = user

    def __contains__(self, user_id: int) -> bool:
        return user_id in self._users

    def __len__(self) -> int:
        return len(self._users)

    def __iter__(self):
        return iter(self._users)

    def get(self, user_id: int, default: Optional[User] = None) -> Optional[User]:
        return self._users.get(user_id, default)

    def keys(self):
        return self._users.keys()

    def values(self):
        return self._users.values()

    def items(self):
        return self._users.items()

    def to_list(self) -> List[Dict[str, Any]]:
        return [user.to_dict() for user in self._users.values()]

    def to_dict(self) -> Dict[int, Dict[str, Any]]:
        return {uid: user.to_dict() for uid, user in self._users.items()}

    def to_json(self, indent: Optional[int] = 2, as_list: bool = True) -> str:
        data = self.to_list() if as_list else self.to_dict()
        return json.dumps(data, indent=indent, default=str)
