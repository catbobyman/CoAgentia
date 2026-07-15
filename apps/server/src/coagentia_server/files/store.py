"""文件数据目录布局与孤儿 GC（契约 D §9.1/§9.2）。

布局（相对数据根 `~/.coagentia/server/`，测试注入临时根）：
- `files-staging/<upload_id>`        预上传正文
- `files-staging/<upload_id>.json`   sidecar 元数据（name/mime/size_bytes/sha256/workspace_id）
- `files/<file_id>`                  绑定后正文（files.stored_path = "files/<file_id>"）

预上传**不落 files 表**（该表 message_id NOT NULL 且不可变，契约 A §4.2）：正文 + sidecar 落盘，
`FilePublic.id = upload_id`、message_id/channel_id = null（staging 态）。发消息带 file_ids 时
**同事务**落 files 行并把正文移入 files/。GC：启动时 + 每小时扫 staging，mtime 超 24h 未绑定
（即仍在 staging 目录）者删除（含 sidecar），写 diagnostic_events(type='system.file_gc')。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from coagentia_server.ledger.service import now_iso

STAGING_DIRNAME = "files-staging"
FILES_DIRNAME = "files"
STAGING_MAX_AGE_SEC = 24 * 60 * 60  # 契约 D §9.2：24h 未绑定 → GC


@dataclass(frozen=True)
class StagedMeta:
    """sidecar 元数据（契约 D §9.2 明列字段）。"""

    name: str
    mime: str
    size_bytes: int
    sha256: str
    workspace_id: str


class FileStore:
    """封装 staging/绑定/读取/GC 的磁盘操作。数据根可注入（测试用临时目录）。"""

    def __init__(self, data_root: str | Path) -> None:
        self.root = Path(data_root)
        self.staging_dir = self.root / STAGING_DIRNAME
        self.files_dir = self.root / FILES_DIRNAME

    def ensure_dirs(self) -> None:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- staging

    def stage(self, upload_id: str, content: bytes, meta: StagedMeta) -> None:
        """正文写 files-staging/<upload_id>，sidecar 写 <upload_id>.json。"""
        self.ensure_dirs()
        (self.staging_dir / upload_id).write_bytes(content)
        (self.staging_dir / f"{upload_id}.json").write_text(
            json.dumps(
                {
                    "name": meta.name,
                    "mime": meta.mime,
                    "size_bytes": meta.size_bytes,
                    "sha256": meta.sha256,
                    "workspace_id": meta.workspace_id,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def read_staged_meta(self, upload_id: str) -> StagedMeta | None:
        sidecar = self.staging_dir / f"{upload_id}.json"
        if not sidecar.exists():
            return None
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        return StagedMeta(
            name=raw["name"],
            mime=raw["mime"],
            size_bytes=raw["size_bytes"],
            sha256=raw["sha256"],
            workspace_id=raw["workspace_id"],
        )

    def is_staged(self, upload_id: str) -> bool:
        return (self.staging_dir / upload_id).exists()

    # ---------------------------------------------------------------- 绑定

    def bind(self, file_id: str) -> str:
        """把 staging 正文移入 files/<file_id>，暂留 sidecar；返回 stored_path 相对路径。

        调用方在数据库提交后调用 finalize_bind；回滚时调用 rollback_bind。sidecar 在提交前保留，
        让数据库失败可以把正文无损搬回 staging，而不是制造最终目录孤儿。
        """
        self.ensure_dirs()
        src = self.staging_dir / file_id
        dst = self.files_dir / file_id
        if not src.exists():
            raise FileNotFoundError(src)
        src.replace(dst)
        return f"{FILES_DIRNAME}/{file_id}"

    def finalize_bind(self, file_id: str) -> None:
        """数据库提交成功后删除 staging sidecar，完成绑定。"""
        sidecar = self.staging_dir / f"{file_id}.json"
        if sidecar.exists():
            sidecar.unlink()

    def rollback_bind(self, file_id: str) -> None:
        """数据库回滚时把正文恢复到 staging；sidecar 在 bind 阶段始终保留。"""
        src = self.files_dir / file_id
        dst = self.staging_dir / file_id
        if src.exists() and not dst.exists():
            src.replace(dst)

    # ---------------------------------------------------------------- 读取

    def content_path(self, stored_path: str) -> Path:
        return self.root / stored_path

    def read_bound(self, stored_path: str) -> bytes | None:
        p = self.content_path(stored_path)
        return p.read_bytes() if p.exists() else None

    # ---------------------------------------------------------------- GC

    def scan_orphans(self, *, now: float | None = None) -> list[str]:
        """返回 mtime 超 24h 的孤儿 upload_id 列表（仍在 staging = 未绑定）。"""
        if not self.staging_dir.exists():
            return []
        cutoff = (now if now is not None else time.time()) - STAGING_MAX_AGE_SEC
        orphans: list[str] = []
        for entry in self.staging_dir.iterdir():
            if entry.suffix == ".json":  # sidecar 随正文一并处理
                continue
            if not entry.is_file():
                continue
            if entry.stat().st_mtime < cutoff:
                orphans.append(entry.name)
        return orphans

    def delete_staged(self, upload_id: str) -> StagedMeta | None:
        """删除 staging 正文 + sidecar，返回其 sidecar 元数据（供 GC 诊断留痕）。"""
        meta = self.read_staged_meta(upload_id)
        body = self.staging_dir / upload_id
        sidecar = self.staging_dir / f"{upload_id}.json"
        if body.exists():
            body.unlink()
        if sidecar.exists():
            sidecar.unlink()
        return meta


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# now_iso 在诊断写入处复用（避免 GC 处再引入时间实现）。
__all__ = [
    "FILES_DIRNAME",
    "STAGING_DIRNAME",
    "STAGING_MAX_AGE_SEC",
    "FileStore",
    "StagedMeta",
    "now_iso",
    "sha256_hex",
]
