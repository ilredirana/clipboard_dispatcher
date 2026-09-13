"""内存存储，保存带 TTL 的文本和图片剪贴板条目。"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class ClipboardKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class SyncDirection(StrEnum):
    """同步操作方向。"""

    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass(frozen=True)
class ClipboardEntry:
    """已验证的单个剪贴板对象。"""

    kind: ClipboardKind
    content: bytes
    mime_type: str
    created_at: float
    source_device_id: str

    @property
    def size(self) -> int:
        """返回内容的字节数。"""
        return len(self.content)


@dataclass(frozen=True)
class SyncActivity:
    """控制台展示的单次同步操作。"""

    device_id: str
    source_device_id: str
    direction: SyncDirection
    kind: ClipboardKind
    occurred_at: float


@dataclass(frozen=True)
class SyncActivitySnapshot:
    """当前进程的同步活动统计。"""

    cross_device_sync_count: int
    recent_activities: tuple[SyncActivity, ...]


class MemoryStorage:
    """线程安全的内存剪贴板存储，只保留最新未过期对象。"""

    def __init__(self, ttl: int):
        self._ttl = ttl
        self._entry: ClipboardEntry | None = None
        self._lock = threading.Lock()
        self._cross_device_sync_count = 0
        self._completed_target_device_ids: set[str] = set()
        self._recent_activities: deque[SyncActivity] = deque(maxlen=8)

    @property
    def ttl(self) -> int:
        """返回当前 TTL 秒数。"""
        return self._ttl

    @ttl.setter
    def ttl(self, value: int) -> None:
        """设置 TTL 秒数。"""
        if value <= 0:
            raise ValueError("TTL 必须大于 0")
        with self._lock:
            self._ttl = value
            self._remove_expired_locked(time.time())

    def set_text(self, content: str, source_device_id: str) -> ClipboardEntry:
        """写入 UTF-8 文本并返回创建的条目。"""
        return self._set(ClipboardKind.TEXT, content.encode("utf-8"), "text/plain; charset=utf-8", source_device_id)

    def set_image(self, content: bytes, mime_type: str, source_device_id: str) -> ClipboardEntry:
        """写入已验证的图片并返回创建的条目。"""
        return self._set(ClipboardKind.IMAGE, content, mime_type, source_device_id)

    def _set(self, kind: ClipboardKind, content: bytes, mime_type: str, source_device_id: str) -> ClipboardEntry:
        """写入内容并覆盖之前的条目。"""
        now = time.time()
        with self._lock:
            entry = ClipboardEntry(
                kind=kind,
                content=content,
                mime_type=mime_type,
                created_at=now,
                source_device_id=source_device_id,
            )
            self._entry = entry
            self._completed_target_device_ids.clear()
            self._recent_activities.appendleft(
                SyncActivity(
                    device_id=source_device_id,
                    source_device_id=source_device_id,
                    direction=SyncDirection.UPLOAD,
                    kind=kind,
                    occurred_at=now,
                )
            )
        return entry

    def record_download(self, entry: ClipboardEntry, device_id: str) -> None:
        """记录一次内容拉取，并统计首次跨设备条目配对。"""
        now = time.time()
        with self._lock:
            self._recent_activities.appendleft(
                SyncActivity(
                    device_id=device_id,
                    source_device_id=entry.source_device_id,
                    direction=SyncDirection.DOWNLOAD,
                    kind=entry.kind,
                    occurred_at=now,
                )
            )
            if device_id == entry.source_device_id:
                return
            if self._entry is entry and device_id not in self._completed_target_device_ids:
                self._completed_target_device_ids.add(device_id)
                self._cross_device_sync_count += 1

    def get_sync_activity_snapshot(self) -> SyncActivitySnapshot:
        """返回当前进程保留的同步活动和跨设备同步次数。"""
        with self._lock:
            return SyncActivitySnapshot(
                cross_device_sync_count=self._cross_device_sync_count,
                recent_activities=tuple(self._recent_activities),
            )

    def get(self) -> ClipboardEntry | None:
        """返回最新的未过期条目。"""
        with self._lock:
            self._remove_expired_locked(time.time())
            return self._entry

    def _remove_expired_locked(self, now: float) -> None:
        """清理到期的当前条目，调用方必须已持有锁。"""
        if self._entry is not None and now - self._entry.created_at > self._ttl:
            self._entry = None
