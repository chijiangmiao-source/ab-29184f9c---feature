"""稳定审计标识的幂等存储。

语义：
- 同一 audit_id + 完全相同载荷：返回首次处理的原记录（包括首次的
  状态、结果与错误体），不重复求解、不重复计费。
- 同一 audit_id + 不同载荷：拒绝（409），并回显指纹差异。
审计记录持久化到磁盘 JSON 文件，服务重启后仍可复算/查重。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Dict, Optional

_FP_PREFIX = "﻿fp-v1"  # 防止 {"a":1} 与 {"a":"1"} 之类的键序碰撞


def fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256((_FP_PREFIX + canonical).encode("utf-8")).hexdigest()


class AuditError(Exception):
    def __init__(self, message: str, existing: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.existing = existing


class AuditStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._records: Dict[str, Dict[str, Any]] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._records = data
            except (json.JSONDecodeError, OSError):
                self._records = {}

    def _flush_locked(self) -> None:
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self.path)

    def get(self, audit_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._records.get(audit_id)
            return dict(rec) if rec is not None else None

    def lookup(self, audit_id: str, payload: Any
               ) -> Optional[Dict[str, Any]]:
        """命中返回原记录；同 id 不同载荷抛 AuditError；未命中返回 None。"""
        fp = fingerprint(payload)
        with self._lock:
            rec = self._records.get(audit_id)
            if rec is None:
                return None
            if rec["payload_fingerprint"] != fp:
                raise AuditError(
                    f"审计标识 {audit_id!r} 已用于不同载荷，拒绝复用。"
                    "同标识重传必须携带完全相同的载荷。",
                    existing={
                        "audit_id": audit_id,
                        "payload_fingerprint": rec["payload_fingerprint"],
                        "status": rec["status"],
                    },
                )
            rec["replayed"] = True
            return dict(rec)

    def save(self, audit_id: str, payload: Any, status: str,
             result: Dict[str, Any]) -> Dict[str, Any]:
        fp = fingerprint(payload)
        with self._lock:
            existing = self._records.get(audit_id)
            if existing is not None:
                if existing["payload_fingerprint"] != fp:
                    raise AuditError(
                        f"审计标识 {audit_id!r} 已用于不同载荷，拒绝复用。",
                        existing={
                            "audit_id": audit_id,
                            "payload_fingerprint":
                                existing["payload_fingerprint"],
                            "status": existing["status"],
                        },
                    )
                existing["replayed"] = True
                return dict(existing)
            rec = {
                "audit_id": audit_id,
                "payload_fingerprint": fp,
                "status": status,
                "replayed": False,
                "result": result,
            }
            self._records[audit_id] = rec
            self._flush_locked()
            return dict(rec)
