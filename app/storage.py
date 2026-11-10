import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.errors import AppError


class LocalStorage:
    def __init__(self, base: Path):
        self.base = Path(base).resolve()

    def path(self, rel: str) -> Path:
        try:
            target = (self.base / rel).resolve()
            if not target.is_relative_to(self.base) or target == self.base:
                raise ValueError
            return target
        except (ValueError, OSError, RuntimeError):
            raise AppError(400, "BAD_PATH", "路径不合法") from None

    def save(self, rel: str, data: bytes) -> str:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return rel

    def read(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def exists(self, rel: str) -> bool:
        return self.path(rel).is_file()

    def list(self) -> list[dict]:
        items = []
        for candidate in self.base.rglob("*.xlsx"):
            path = self.path(candidate.relative_to(self.base).as_posix())
            if not path.is_file():
                continue
            stat = path.stat()
            items.append((stat.st_mtime, {
                "rel": candidate.relative_to(self.base).as_posix(), "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, ZoneInfo("Asia/Shanghai"))
                .strftime("%Y-%m-%d %H:%M:%S"),
            }))
        return [item for _, item in sorted(items, key=lambda pair: pair[0], reverse=True)]


def get_storage() -> LocalStorage:
    settings = get_settings()
    if settings.storage == "cos":
        raise NotImplementedError("COS 存储尚未实现")
    if settings.storage != "local":
        raise ValueError("不支持的存储方式")
    return LocalStorage(settings.export_dir)
