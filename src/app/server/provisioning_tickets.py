"""使用 SQLite 原子核销短时配置票据，仅持久化票据和设备凭据的摘要。"""

import hashlib
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import config_manager

TaskerArtifact = Literal["project", "upload", "download"]
TICKET_TTL_SECONDS = 600
_TICKET_MATCH = "ticket_hash = ? AND device_id = ? AND artifact = ? AND token_hash = ? AND expires_at > ?"


def get_store_path() -> Path:
    """将票据数据库放在当前初始化配置的同一目录中。"""
    return Path(config_manager.get_config_path()).with_name("provisioning_tickets.sqlite3")


def initialize_store(path: Path) -> None:
    """创建仅存储摘要和授权范围的票据表。"""
    with closing(sqlite3.connect(path, timeout=5.0)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS provisioning_tickets ("
            "ticket_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL, artifact TEXT NOT NULL, "
            "token_hash TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ticket_expiry ON provisioning_tickets (expires_at)")


def secret_digest(value: str) -> str:
    """计算高熵随机凭据的 SHA256 摘要。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_ticket(
    path: Path, device_id: str, artifact: TaskerArtifact, token_hash: str, now: float,
) -> tuple[str, float]:
    """生成 256 位随机票据，清理过期记录并保存摘要。"""
    ticket = secrets.token_urlsafe(32)
    expires_at = now + TICKET_TTL_SECONDS
    with closing(sqlite3.connect(path, timeout=5.0)) as connection, connection:
        connection.execute("DELETE FROM provisioning_tickets WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO provisioning_tickets VALUES (?, ?, ?, ?, ?)",
            (secret_digest(ticket), device_id, artifact, token_hash, expires_at),
        )
    return ticket, expires_at


def ticket_parameters(
    ticket: str, device_id: str, artifact: TaskerArtifact, token_hash: str, now: float,
) -> tuple[str, str, str, str, float]:
    """生成严格绑定设备、资产、当前凭据和有效期的查询参数。"""
    return secret_digest(ticket), device_id, artifact, token_hash, now


def ticket_is_valid(
    path: Path, ticket: str, device_id: str, artifact: TaskerArtifact, token_hash: str, now: float,
) -> bool:
    """检查票据授权，二维码预览不会核销票据。"""
    with closing(sqlite3.connect(path, timeout=5.0)) as connection:
        row = connection.execute(
            f"SELECT 1 FROM provisioning_tickets WHERE {_TICKET_MATCH}",
            ticket_parameters(ticket, device_id, artifact, token_hash, now),
        ).fetchone()
    return row is not None


def consume_ticket(
    path: Path, ticket: str, device_id: str, artifact: TaskerArtifact, token_hash: str, now: float,
) -> bool:
    """以单次数据库删除原子核销票据，并发下载只允许一个请求成功。"""
    with closing(sqlite3.connect(path, timeout=5.0)) as connection, connection:
        consumed = connection.execute(
            f"DELETE FROM provisioning_tickets WHERE {_TICKET_MATCH}",
            ticket_parameters(ticket, device_id, artifact, token_hash, now),
        ).rowcount
    return consumed == 1


def revoke_device_tickets(path: Path, device_id: str) -> None:
    """设备启用状态发生变化时撤销所有未使用的配置票据。"""
    with closing(sqlite3.connect(path, timeout=5.0)) as connection, connection:
        connection.execute("DELETE FROM provisioning_tickets WHERE device_id = ?", (device_id,))
