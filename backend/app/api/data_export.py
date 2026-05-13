"""GDPR / 个人信息保护法 — user-driven data export.

GET /me/data/export returns a JSON snapshot of every row our DB
holds about the calling user. We deliberately keep this human-readable
JSON (vs zip) so users can inspect it directly; iOS shares it via the
system share sheet.

Rows included:
  - users           — your account
  - subscriptions   — purchase history (no Apple JWS payload)
  - usage_periods   — quota windows
  - usage_records   — every analyze you ran (step config + proposals)
  - admin_audit_log — only if you're an admin (your own actions)

NOT included (and the response says so explicitly):
  - Original photo frames or video — we never persist them
  - Other users' data
  - OTP plain codes — we only store HMAC hashes, none recoverable
  - Apple's signed JWS — opaque blob, no extra info beyond what's
    already in subscriptions
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response

from ..services import admin_audit
from ..services import auth as auth_svc
from ..services import user_repo

log = logging.getLogger(__name__)
router = APIRouter(tags=["privacy"])


def _rows(con: sqlite3.Connection, sql: str, *args: Any) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args).fetchall()]


@router.get("/me/data/export")
async def export_my_data(
    user: auth_svc.CurrentUser = Depends(auth_svc.current_user),
) -> Response:
    with user_repo._connect() as con:                               # noqa: SLF001
        users = _rows(con, "SELECT * FROM users WHERE id = ?", user.id)
        subs = _rows(con,
                     "SELECT id, product_id, environment, purchase_date, "
                     "expires_at, revoked_at, auto_renew "
                     "FROM subscriptions WHERE user_id = ? "
                     "ORDER BY purchase_date DESC",
                     user.id)
        periods = _rows(con,
                        "SELECT plan, period_start, period_end, total, used, "
                        "created_at, updated_at FROM usage_periods "
                        "WHERE user_id = ? ORDER BY created_at DESC",
                        user.id)
        records = _rows(con,
                        "SELECT id, request_id, status, charge_at, refund_at, "
                        "step_config, proposals, picked_proposal_id, picked_at, "
                        "captured, captured_at, model_id, prompt_tokens, "
                        "completion_tokens, cost_usd, error_code, created_at "
                        "FROM usage_records WHERE user_id = ? "
                        "ORDER BY created_at DESC",
                        user.id)
        admin_actions = []
        if user.role == "admin":
            admin_actions = _rows(
                con,
                "SELECT id, action, target, payload, occurred_at "
                "FROM admin_audit_log WHERE admin_id = ? "
                "ORDER BY id DESC LIMIT 500",
                user.id,
            )

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user.id,
        "disclosure": {
            "what_we_store_for_you":
                "账户基本信息(手机号或邮箱+登录方式)、订阅记录、配额周期、"
                "每次分析的四步配置和文本结果。",
            "anonymized_aggregates_for_product_improvement":
                "我们会基于您的非个人化使用数据（如选择的拍摄场景、画质偏好、"
                "风格关键词、采纳的出片方案）做聚合统计以改进 App。"
                "聚合结果不包含您的账号 ID、不可被反推，且对每个分组应用 "
                "k-匿名 ≥5 阈值。删除账号后，相关原始记录立刻随账号一并清理。",
            "what_we_do_not_store":
                "原始照片像素、原始视频帧、Apple 签名 JWS 原文、OTP 明文。",
            "how_long_we_keep_it":
                "账户数据：直到你主动删除；分析记录：默认 12 个月后归档；"
                "OTP HMAC 哈希：5 分钟后失效。",
            "how_to_delete":
                "在 App「账户与订阅」页点「删除我的账号」会立刻软删除并在 24 小时内硬删。",
        },
        "users": users,
        "subscriptions": subs,
        "usage_periods": periods,
        "usage_records": records,
        "admin_audit_log": admin_actions,
    }

    body = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    headers = {
        "Content-Disposition":
            f'attachment; filename="ai-photo-coach-export-{user.id}.json"',
    }
    # v17e — sensitive: this dumps every row we hold for the user.
    # Audit so admins can spot anomalies (e.g. an account being
    # exported repeatedly hours before deletion = exfil red flag).
    admin_audit.write(
        f"user:{user.id}", "user.data_export", target=user.id,
        payload={"records": len(records),
                  "subscriptions": len(subs),
                  "bytes": len(body)},
    )
    return Response(content=body, media_type="application/json", headers=headers)
