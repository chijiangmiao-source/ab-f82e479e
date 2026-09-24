#!/usr/bin/env python3
"""Real HTTP smoke test against a running uvicorn server.

Exercises over the network (not in-process):

  1. GET  /health
  2. POST /api/solve with a gauge-block *preemption counterexample*:
     choosing the closest combination greedily for the first gap would
     steal the only feasible blocks for the second gap; the joint exact
     optimum must still be found.
  3. POST /api/solve at the *no-solution boundary* (a gap needing > 6
     blocks) -> 422 with a field-level reason and the input echoed back.

Usage: smoke_http.py [base_url]
Exit code 0 on success, 1 on any failure.
"""
from __future__ import annotations

import json
import sys

import httpx

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def fail(msg: str) -> None:
    print(f"[SMOKE][FAIL] {msg}")
    sys.exit(1)


def main() -> None:
    # 1. health ------------------------------------------------------------
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
    except Exception as exc:  # network error
        fail(f"health request error: {exc}")
    if r.status_code != 200 or r.json().get("status") != "ok":
        fail(f"health bad response: {r.status_code} {r.text}")
    print(f"[SMOKE] GET /health -> {r.status_code} {r.text.strip()}")

    # 2. preemption counterexample -----------------------------------------
    # Gap A target 10: {4,6} is the unique dev-0 combination.
    # Gap B target 10 +/-0: its ONLY feasible combination is also {4,6}.
    # A greedy/independent choice for A steals B's only fit and would make
    # the batch infeasible (or worse). The joint optimum exists: A takes
    # block 11 (dev 1), B takes {4,6} (dev 0). Max dev = 1 globally.
    preemption = {
        "blocks": [
            {"label": f"B{i}", "length": v}
            for i, v in enumerate([4, 6, 11, 20, 30, 40, 50, 60])
        ],
        "targets": [
            {"length": 10, "tolerance": 1},
            {"length": 10, "tolerance": 0},
        ],
    }
    r = httpx.post(f"{BASE}/api/solve", json=preemption, timeout=15)
    if r.status_code != 200:
        fail(f"preemption case expected 200, got {r.status_code}: {r.text}")
    data = r.json()
    if not data.get("ok"):
        fail(f"preemption case not ok: {r.text}")
    res = data["result"]
    obj = res["objective"]
    if obj["max_abs_dev"] != 1 or obj["sum_abs_dev"] != 1:
        fail(f"preemption joint optimum wrong: {obj}")
    # exact arbitrary-precision count delivered as a decimal string
    if not isinstance(res["cooptimal_count"], str) or not (
        res["cooptimal_count"].isdigit()
    ):
        fail(f"cooptimal_count not an exact decimal string: {res}")
    gap_b = res["gaps"][1]
    if gap_b["blocks"] != ["B0", "B1"] or gap_b["deviation"] != 0:
        fail(f"gap B must get B0,B1 exactly: {gap_b}")
    fates = {f["label"]: f for f in res["block_fates"]}
    if fates["B0"]["kind"] != "fixed" or fates["B0"]["gap"] != 1:
        fail(f"B0 fate wrong: {fates['B0']}")
    print(
        "[SMOKE] preemption counterexample solved: "
        f"D*={obj['max_abs_dev']} S*={obj['sum_abs_dev']} "
        f"U*={obj['used_blocks']} co-opt={res['cooptimal_count']}"
    )

    # 3. no-solution boundary ----------------------------------------------
    # Eight unit blocks; first gap target 8 would require all 8 blocks, but
    # each gap may use at most 6 -> no feasible assignment.
    boundary = {
        "blocks": [{"label": f"U{i}", "length": 1} for i in range(8)],
        "targets": [
            {"length": 8, "tolerance": 0},
            {"length": 1, "tolerance": 0},
        ],
    }
    r = httpx.post(f"{BASE}/api/solve", json=boundary, timeout=15)
    if r.status_code != 422:
        fail(f"boundary case expected 422, got {r.status_code}: {r.text}")
    body = r.json()
    if body.get("ok") is not False:
        fail("boundary case must carry ok=false")
    fields = {e["field"] for e in body.get("errors", [])}
    if "targets[0]" not in fields:
        fail(f"boundary case missing field-level reason targets[0]: {fields}")
    # failed input must be retained/echoed, never silently discarded
    if body.get("input") != boundary:
        fail("boundary case must echo the submitted input unchanged")
    print(
        "[SMOKE] no-solution boundary -> 422, field="
        f"{sorted(fields)}, input echoed"
    )

    # 4. validation error also retains input (duplicate label) -------------
    dup = json.loads(json.dumps(preemption))
    dup["blocks"][3]["label"] = "B0"
    r = httpx.post(f"{BASE}/api/solve", json=dup, timeout=15)
    if r.status_code != 422 or r.json().get("input") != dup:
        fail("duplicate-label case must be 422 and echo input")
    print("[SMOKE] duplicate-label validation -> 422, input echoed")

    print("[SMOKE][OK] all HTTP smoke assertions passed")


if __name__ == "__main__":
    main()
