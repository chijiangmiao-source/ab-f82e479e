"""FastAPI service for exact gauge-block gap allocation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .solver import Block, InfeasibleError, Target, feasible_groups, solve

app = FastAPI(title="Gauge Block Allocation", version="1.0.0")

MIN_BLOCKS, MAX_BLOCKS = 8, 16
MIN_TARGETS, MAX_TARGETS = 2, 4
MAX_BLOCK_SIZE = 6
LEN_MIN, LEN_MAX = 1, 10**12
TOL_MIN, TOL_MAX = 0, 10**12
LABEL_MAX = 40


# ---------------------------------------------------------------------------
# Validation (field-level errors, input echoed back on failure)
# ---------------------------------------------------------------------------


def _is_strict_int(v: Any) -> bool:
    # bool is a subclass of int but must not be accepted as a length.
    return isinstance(v, int) and not isinstance(v, bool)


_INT_RE = None


def _coerce_int(v: Any):
    """Accept an int or a strict decimal-integer string ("10", "-3").

    Floats, bools, "3.5", "10.0", "1e2" and blank values are rejected
    (returning None) so they surface as field-level non-integer errors.
    """
    import re
    global _INT_RE
    if _is_strict_int(v):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if _INT_RE is None:
        _INT_RE = re.compile(r"^[+-]?\d+$")
    if not _INT_RE.match(s):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _validate(payload: Any) -> tuple[list[Block], list[Target], list[dict]]:
    errors: list[dict] = []

    if not isinstance(payload, dict):
        return [], [], [{"field": "_root", "message": "请求体必须是 JSON 对象"}]

    raw_blocks = payload.get("blocks")
    raw_targets = payload.get("targets")

    blocks: list[Block] = []
    targets: list[Target] = []
    typed_block_rows: list[tuple[str, int]] = []
    typed_target_rows: list[tuple[int, int]] = []

    # ---- blocks -----------------------------------------------------------
    if not isinstance(raw_blocks, list):
        errors.append({"field": "blocks",
                       "message": f"必须提供 {MIN_BLOCKS}-{MAX_BLOCKS} 个量块数组"})
    else:
        if not (MIN_BLOCKS <= len(raw_blocks) <= MAX_BLOCKS):
            errors.append({
                "field": "blocks",
                "message": f"量块数量必须在 {MIN_BLOCKS} 到 {MAX_BLOCKS} 之间，"
                           f"当前 {len(raw_blocks)}",
            })
        seen_labels: set[str] = set()
        for i, item in enumerate(raw_blocks):
            base = f"blocks[{i}]"
            if not isinstance(item, dict):
                errors.append({"field": base, "message": "量块必须是对象"})
                continue
            label = item.get("label")
            if not isinstance(label, str) or not label.strip():
                errors.append({"field": f"{base}.label",
                               "message": "标识必须是非空字符串"})
            else:
                label = label.strip()
                if len(label) > LABEL_MAX:
                    errors.append({"field": f"{base}.label",
                                   "message": f"标识长度不得超过 {LABEL_MAX}"})
                elif label in seen_labels:
                    errors.append({"field": f"{base}.label",
                                   "message": f"标识 {label!r} 重复，标识必须唯一"})
                seen_labels.add(label)
            length = _coerce_int(item.get("length"))
            if length is None:
                errors.append({"field": f"{base}.length",
                               "message": "长度必须是整数纳米值（不接受小数）"})
            elif not (LEN_MIN <= length <= LEN_MAX):
                errors.append({
                    "field": f"{base}.length",
                    "message": f"长度必须在 {LEN_MIN} 到 {LEN_MAX} 纳米之间",
                })
            if isinstance(label, str) and label.strip() and length is not None:
                typed_block_rows.append((label.strip(), length))

    # ---- targets ----------------------------------------------------------
    if not isinstance(raw_targets, list):
        errors.append({"field": "targets",
                       "message": f"必须提供 {MIN_TARGETS}-{MAX_TARGETS} 个目标间隙数组"})
    else:
        if not (MIN_TARGETS <= len(raw_targets) <= MAX_TARGETS):
            errors.append({
                "field": "targets",
                "message": f"目标间隙数量必须在 {MIN_TARGETS} 到 {MAX_TARGETS} 之间，"
                           f"当前 {len(raw_targets)}",
            })
        for i, item in enumerate(raw_targets or []):
            base = f"targets[{i}]"
            if not isinstance(item, dict):
                errors.append({"field": base, "message": "目标间隙必须是对象"})
                continue
            length = _coerce_int(item.get("length"))
            if length is None:
                errors.append({"field": f"{base}.length",
                               "message": "目标长度必须是整数（不接受小数）"})
            elif not (LEN_MIN <= length <= LEN_MAX):
                errors.append({
                    "field": f"{base}.length",
                    "message": f"目标长度必须在 {LEN_MIN} 到 {LEN_MAX} 纳米之间",
                })
            tol = _coerce_int(item.get("tolerance"))
            if tol is None:
                errors.append({"field": f"{base}.tolerance",
                               "message": "允许偏差必须是非负整数（不接受小数）"})
            elif not (TOL_MIN <= tol <= TOL_MAX):
                errors.append({
                    "field": f"{base}.tolerance",
                    "message": f"允许偏差必须在 {TOL_MIN} 到 {TOL_MAX} 之间",
                })
            if length is not None and tol is not None:
                typed_target_rows.append((length, tol))

    if errors:
        return [], [], errors

    # Build label-sorted block model objects.
    typed_block_rows.sort(key=lambda x: x[0])
    blocks = [Block(i, label, length)
              for i, (label, length) in enumerate(typed_block_rows)]
    targets = [Target(length, tol) for length, tol in typed_target_rows]
    return blocks, targets, []


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _serialise(blocks: list[Block], targets: list[Target], res: dict) -> dict:
    labels = [b.label for b in blocks]

    gaps = []
    for j, ids in enumerate(res["assignment"]):
        member_labels = [labels[i] for i in ids]
        actual = targets[j].length + res["deviations"][j]
        gaps.append({
            "index": j,
            "target_length": targets[j].length,
            "tolerance": targets[j].tolerance,
            "actual_length": actual,
            "deviation": res["deviations"][j],
            "within_tolerance":
                abs(res["deviations"][j]) <= targets[j].tolerance,
            "blocks": member_labels,
        })

    fates = []
    for st in res["statuses"]:
        bit = st["block_index"]
        fates.append({
            "label": labels[bit],
            "length": blocks[bit].length,
            "kind": st["kind"],  # fixed | flexible | unused
            "gap": st["gap"],
            "gaps": st["gaps"],
            "can_be_unused": st["can_be_unused"],
        })

    return {
        "blocks": [{"label": b.label, "length": b.length} for b in blocks],
        "targets": [{"length": t.length, "tolerance": t.tolerance}
                    for t in targets],
        "objective": res["objective"],
        # arbitrary-precision count as a decimal string
        "cooptimal_count": str(int(res["cooptimal_count"])),
        "gaps": gaps,
        "unused_blocks": [labels[i] for i in res["unused"]],
        "block_fates": fates,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/solve")
async def solve_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "errors": [{"field": "_root",
                            "message": "请求体不是合法的 JSON"}],
                "input": None,
            },
        )

    blocks, targets, errors = _validate(payload)
    if errors:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors, "input": payload},
        )

    try:
        res = solve(blocks, targets)
    except InfeasibleError as exc:
        # Field-level reason: pinpoint a gap with no feasible subset if
        # possible, otherwise report the disjointness conflict globally.
        per_gap = [feasible_groups(blocks, t) for t in targets]
        field_errors = []
        for j, g in enumerate(per_gap):
            if not g:
                field_errors.append({
                    "field": f"targets[{j}]",
                    "message": (
                        f"目标间隙 {j + 1} 不存在由 1-{MAX_BLOCK_SIZE} 块"
                        "组成且偏差不越界的组合，无法参与任何分配"),
                })
        if not field_errors:
            field_errors.append({
                "field": "allocation",
                "message": (
                    "每个间隙单独都存在可行组合，但无法选出互不共用量块"
                    "的联合分配（每个间隙 1-6 块），故无可行分配"),
            })
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": field_errors, "input": payload},
        )

    return JSONResponse(
        status_code=200,
        content={"ok": True, "result": _serialise(blocks, targets, res)},
    )


# Static frontend (present in the production image; optional locally).
_STATIC_DIR = Path(os.environ.get(
    "STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static")))
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True),
              name="static")
