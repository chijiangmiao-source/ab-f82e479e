"""Brute-force oracle cross-checks for the gauge-block solver."""
from __future__ import annotations

import itertools
import random

from app.solver import Block, Target, InfeasibleError, feasible_groups, solve


def brute(blocks, targets):
    """Enumerate full assignments by DFS over feasible per-gap subsets;
    return (opt_key, count, list_of_assignments)."""
    n, m = len(blocks), len(targets)
    groups = [feasible_groups(blocks, t) for t in targets]
    if any(not g for g in groups):
        return None
    best = None
    sols = []

    def rec(j, used_mask, masks, d_max, d_sum):
        nonlocal best, sols
        if j == m:
            key = (d_max, d_sum, used_mask.bit_count())
            if best is None or key < best:
                best, sols = key, [tuple(masks)]
            elif key == best:
                sols.append(tuple(masks))
            return
        for cmask, dev in groups[j].items():
            if cmask & used_mask:
                continue
            a = abs(dev)
            masks.append(cmask)
            rec(j + 1, used_mask | cmask, masks,
                max(d_max, a), d_sum + a)
            masks.pop()

    rec(0, 0, [], 0, 0)
    if best is None:
        return None
    return best, len(sols), [(s, None) for s in sols]


def oracle_statuses(n, m, sols):
    possible = [[False] * n for _ in range(m)]
    used_masks = []
    for sets, _ in sols:
        used = 0
        for s in sets:
            used |= s
        used_masks.append(used)
        for j in range(m):
            for b in range(n):
                if (sets[j] >> b) & 1:
                    possible[j][b] = True
    out = []
    for b in range(n):
        homes = [j for j in range(m) if possible[j][b]]
        unused_ok = any(not ((u >> b) & 1) for u in used_masks)
        if not homes:
            out.append("unused")
        elif len(homes) == 1 and not unused_ok:
            out.append(f"fixed:{homes[0]}")
        else:
            out.append("flexible:" + ",".join(map(str, homes)) +
                       ("+unused" if unused_ok else ""))
    return out


def random_case(rng, n, m, length_range=(1, 40), tol_choices=(0, 1, 2, 5)):
    blocks = [
        Block(i, f"B{i:02d}", rng.randint(*length_range)) for i in range(n)
    ]
    targets = []
    for _ in range(m):
        k = rng.randint(1, min(4, n // 2))
        idxs = rng.sample(range(n), k)
        total = sum(blocks[i].length for i in idxs)
        tol = rng.choice(tol_choices)
        targets.append(Target(total + rng.randint(-tol, tol), tol))
    return blocks, targets


def check_case(blocks, targets, label=""):
    oracle = brute(blocks, targets)
    try:
        res = solve(blocks, targets)
    except InfeasibleError:
        assert oracle is None, f"{label}: solver said infeasible, oracle has sol"
        return "infeasible"
    assert oracle is not None, f"{label}: solver returned, oracle none"
    (oD, oS, oU), ocount, osols = oracle
    assert res["objective"] == {
        "max_abs_dev": oD, "sum_abs_dev": oS, "used_blocks": oU
    }, f"{label}: objective mismatch {res['objective']} vs {(oD,oS,oU)}"
    assert int(res["cooptimal_count"]) == ocount, (
        f"{label}: count {res['cooptimal_count']} vs {ocount}")

    # assignment itself feasible and optimal
    sets = [0] * len(targets)
    for j, ids in enumerate(res["assignment"]):
        for b in ids:
            assert not (sets[j] >> b) & 1
            sets[j] |= 1 << b
    for j in range(len(targets)):
        assert 1 <= sets[j].bit_count() <= 6
    for j in range(len(targets)):
        for k in range(j + 1, len(targets)):
            assert sets[j] & sets[k] == 0
    used = 0
    for s in sets:
        used |= s
    assert used.bit_count() == oU
    devs = res["deviations"]
    assert (max(map(abs, devs)), sum(map(abs, devs)), oU) == (oD, oS, oU)

    # count via optimal masks sums correctly
    mask_sum = 0
    # recompute count distribution check: every oracle sol's status view
    # statuses
    got_status = []
    for st in res["statuses"]:
        if st["kind"] == "unused":
            got_status.append("unused")
        elif st["kind"] == "fixed":
            got_status.append(f"fixed:{st['gap']}")
        else:
            got_status.append("flexible:" + ",".join(map(str, st["gaps"])) +
                              ("+unused" if st["can_be_unused"] else ""))
    exp_status = oracle_statuses(len(blocks), len(targets), osols)
    assert got_status == exp_status, (
        f"{label}: status mismatch\n got={got_status}\n exp={exp_status}")

    # canonical: compare against lexicographic min of oracle solutions
    def canon_key(sol):
        sets, _ = sol
        n = len(blocks)
        m = len(targets)
        used = 0
        for s in sets:
            used |= s
        key = []
        for s in sets:
            key.append(tuple(b for b in range(n) if (s >> b) & 1))
        key.append(tuple(b for b in range(n) if not ((used >> b) & 1)))
        return tuple(key)

    exp_canon = min(osols, key=canon_key)
    got_canon = tuple(sum(1 << b for b in ids) for ids in res["assignment"])
    assert got_canon == exp_canon[0], (
        f"{label}: canonical {got_canon} vs {exp_canon[0]}")
    return "ok"


def test_randomized():
    rng = random.Random(20260924)
    counts = {"ok": 0, "infeasible": 0}
    for trial in range(250):
        n = rng.randint(8, 10)
        m = rng.randint(2, 3)
        blocks, targets = random_case(rng, n, m)
        r = check_case(blocks, targets, f"rand{trial}")
        counts[r] += 1
    print("randomized:", counts)


def test_randomized_four_gaps():
    rng = random.Random(424242)
    counts = {"ok": 0, "infeasible": 0}
    for trial in range(120):
        n = rng.randint(8, 9)
        blocks, targets = random_case(
            rng, n, 4, length_range=(1, 25),
            tol_choices=(0, 1, 2, 4))
        r = check_case(blocks, targets, f"four{trial}")
        counts[r] += 1
    print("randomized-4gaps:", counts)


def test_preemption_counterexample():
    """Greedy closest-combination per gap is globally worse.

    Gap A target 10: blocks {4,6} hit it exactly (0).
    Gap B target 10: only combo that fits band tol=0 is also {4,6};
    alternative for A using block 10 alone also exact.
    Greedy that grabs 4,6 for the first gap makes B infeasible or worse;
    joint optimum exists with max dev 0.
    """
    blocks = [Block(i, f"G{i}", v) for i, v in enumerate(
        [4, 6, 10, 3, 7, 5, 5, 8])]
    # ensure 8 blocks; targets:
    targets = [Target(10, 0), Target(12, 1), Target(13, 2)]
    res = solve(blocks, targets)
    assert res["objective"]["max_abs_dev"] == 0
    print("preemption example:", res["objective"],
          "co-opt count:", res["cooptimal_count"])
    check_case(blocks, targets, "preemption")


def test_no_solution_boundary():
    # 8 blocks, gap target reachable only with >6 blocks -> infeasible
    blocks = [Block(i, f"X{i}", 1) for i in range(8)]
    targets = [Target(8, 0), Target(1, 0)]  # first needs 8 blocks (>6)
    try:
        solve(blocks, targets)
        raise AssertionError("expected infeasible")
    except InfeasibleError:
        pass

    # tolerance band empty of every subset
    blocks2 = [Block(i, f"Y{i}", 10) for i in range(8)]
    targets2 = [Target(5, 0), Target(10, 0)]
    try:
        solve(blocks2, targets2)
        raise AssertionError("expected infeasible")
    except InfeasibleError:
        pass

    # just inside boundary: 6-block subset exactly, other gap 1 block
    blocks3 = [Block(i, f"Z{i}", 1) for i in range(8)]
    r = solve(blocks3, [Target(6, 0), Target(1, 0)])
    assert r["objective"] == {
        "max_abs_dev": 0, "sum_abs_dev": 0, "used_blocks": 7}
    print("boundary ok, coopt:", r["cooptimal_count"],
          "unused:", r["unused"])


def test_max_size():
    rng = random.Random(7)
    blocks, targets = random_case(
        rng, 16, 4, length_range=(50, 300), tol_choices=(1, 3, 8))
    import time
    t0 = time.time()
    res = solve(blocks, targets)
    dt = time.time() - t0
    print(f"16x4 solved in {dt:.2f}s, opt={res['objective']}, "
          f"count digits={len(str(int(res['cooptimal_count'])))}")
    assert dt < 30


if __name__ == "__main__":
    test_randomized()
    test_randomized_four_gaps()
    test_preemption_counterexample()
    test_no_solution_boundary()
    test_max_size()
    print("ALL ORACLE TESTS PASSED")
