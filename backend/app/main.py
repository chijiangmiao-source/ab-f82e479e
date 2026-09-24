"""FastAPI service for optimal gauge-block allocation."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    ID_PATTERN,
    LENGTH_MAX,
    LENGTH_MIN,
    MAX_BLOCKS,
    MAX_BLOCKS_PER_GAP,
    MAX_TARGETS,
    MIN_BLOCKS,
    MIN_TARGETS,
    TARGET_MAX,
    TARGET_MIN,
    TOL_MAX,
    TOL_MIN,
)

SOLVER_BIN = os.environ.get(
    "SOLVER_BIN", os.path.join(os.path.dirname(__file__), "bin", "solver")
)
SOLVER_TIMEOUT = float(os.environ.get("SOLVER_TIMEOUT", "20"))

app = FastAPI(title="量块间隙精密分配", version="1.0.0")


def _is_int(value: Any) -> bool:
    # bool is a subtype of int in Python but is not a valid length here
    return isinstance(value, int) and not isinstance(value, bool)


def _field_errors(payload: Any) -> Tuple[List[dict], dict]:
    """Validate the raw JSON payload, returning (errors, normalized view).

    Raw parsing (instead of relying solely on Pydantic coercion) lets us report
    non-integer and malformed rows as field-level errors while echoing back the
    exact user input.
    """
    errors: List[dict] = []
    raw_blocks = payload.get("blocks") if isinstance(payload, dict) else None
    raw_targets = payload.get("targets") if isinstance(payload, dict) else None

    blocks: List[Dict[str, Any]] = []
    targets: List[Dict[str, Any]] = []

    if not isinstance(raw_blocks, list):
        errors.append({"field": "blocks", "message": "blocks 必须为数组"})
        raw_blocks = []
    if not isinstance(raw_targets, list):
        errors.append({"field": "targets", "message": "targets 必须为数组"})
        raw_targets = []

    n, q = len(raw_blocks), len(raw_targets)
    if not (MIN_BLOCKS <= n <= MAX_BLOCKS):
        errors.append(
            {"field": "blocks",
             "message": f"需要 {MIN_BLOCKS}–{MAX_BLOCKS} 个量块，当前 {n} 个"}
        )
    if not (MIN_TARGETS <= q <= MAX_TARGETS):
        errors.append(
            {"field": "targets",
             "message": f"需要 {MIN_TARGETS}–{MAX_TARGETS} 个目标间隙，当前 {q} 个"}
        )

    seen: set[str] = set()
    for i, row in enumerate(raw_blocks):
        loc = f"blocks[{i}]"
        if not isinstance(row, dict):
            errors.append({"field": loc, "message": "量块条目格式错误"})
            continue
        bid = row.get("id")
        length = row.get("length")
        if not isinstance(bid, str) or not ID_PATTERN.match(bid):
            errors.append({"field": f"{loc}.id",
                           "message": "标识需为 1–32 个非空白字符"})
        elif bid in seen:
            errors.append({"field": f"{loc}.id",
                           "message": f"标识 {bid!r} 重复"})
        else:
            seen.add(bid)
        if not _is_int(length):
            errors.append({"field": f"{loc}.length",
                           "message": "长度必须是整数纳米值（不接受小数）"})
        elif not (LENGTH_MIN <= length <= LENGTH_MAX):
            errors.append(
                {"field": f"{loc}.length",
                 "message": f"长度需在 {LENGTH_MIN}–{LENGTH_MAX} 之间"}
            )
        blocks.append({"id": bid if isinstance(bid, str) else f"#{i}",
                       "length": length if _is_int(length) else 0})

    for i, row in enumerate(raw_targets):
        loc = f"targets[{i}]"
        if not isinstance(row, dict):
            errors.append({"field": loc, "message": "目标条目格式错误"})
            continue
        target = row.get("target")
        tol = row.get("tolerance")
        if not _is_int(target):
            errors.append({"field": f"{loc}.target",
                           "message": "目标间隙必须是整数（不接受小数）"})
        elif not (TARGET_MIN <= target <= TARGET_MAX):
            errors.append(
                {"field": f"{loc}.target",
                 "message": f"目标间隙需在 {TARGET_MIN}–{TARGET_MAX} 之间"}
            )
        if not _is_int(tol):
            errors.append({"field": f"{loc}.tolerance",
                           "message": "允许偏差必须是非负整数（不接受小数）"})
        elif not (TOL_MIN <= tol <= TOL_MAX):
            errors.append(
                {"field": f"{loc}.tolerance",
                 "message": f"允许偏差需在 {TOL_MIN}–{TOL_MAX} 之间"}
            )
        targets.append({
            "target": target if _is_int(target) else 0,
            "tolerance": tol if _is_int(tol) else 0,
        })

    # Necessary cardinality bound: q disjoint non-empty subsets of <= 6 blocks
    # need at least q blocks.
    if not errors and n < q:
        errors.append({"field": "blocks",
                       "message": f"{q} 个间隙至少需要 {q} 个量块"})

    return errors, {"blocks": blocks, "targets": targets}


def run_solver(view: dict) -> dict:
    blocks = view["blocks"]
    targets = view["targets"]
    lines = [
        f"{len(blocks)} {len(targets)}",
        " ".join(b["id"] for b in blocks),
        " ".join(str(b["length"]) for b in blocks),
        " ".join(str(t["target"]) for t in targets),
        " ".join(str(t["tolerance"]) for t in targets),
    ]
    try:
        proc = subprocess.run(
            [SOLVER_BIN],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            timeout=SOLVER_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="求解器未构建")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="求解超时")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise HTTPException(
            status_code=500,
            detail=f"求解器失败: {proc.stderr.strip()[:200]}",
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="求解器输出无法解析")
    result["maxBlocksPerGap"] = MAX_BLOCKS_PER_GAP
    return result


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/solve")
def solve(payload: dict) -> JSONResponse:
    # A failed validation / infeasibility never carries a `solution`; the
    # client keeps its previous successful result on screen untouched.
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": [
                {"field": "_", "message": "请求体必须是 JSON 对象"}]},
        )
    errors, view = _field_errors(payload)
    if errors:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors, "echo": payload},
        )
    result = run_solver(view)
    if not result.get("feasible"):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "errors": [{
                    "field": "targets",
                    "message": "无可行分配：在各间隙允许偏差内、每间隙 1–6 块、"
                               "每块至多用一次的条件下无解",
                }],
                "infeasible": True,
                "echo": payload,
            },
        )
    result["ok"] = True
    return JSONResponse(content=result)


# Static frontend (production build), mounted last so /api and /health win.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/assets",
              StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")),
              name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
