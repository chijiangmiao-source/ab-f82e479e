"""Exact gauge-block allocation solver.

Problem
-------
* n uniquely labelled blocks (8 <= n <= 16), each with an integer length.
* m target gaps (2 <= m <= 4), each with an integer target length and an
  integer tolerance: a gap's realised length must stay within the inclusive
  band ``[target - tol, target + tol]``.
* Every gap receives 1..6 blocks; each block is used at most once; unused
  blocks are allowed.

Lexicographic objective over full assignments (ordered tuple of pairwise
disjoint per-gap subsets; unused blocks are implicit):

  1. minimum maximum absolute deviation         D*,
  2. then minimum sum of absolute deviations     S*,
  3. then minimum number of used blocks          U*.

Tie-break to one canonical assignment: by gap order, ascending block-id
arrays of each gap, then the ascending unused-id array.

Also reported:

  * the exact arbitrary-precision number of co-optimal full assignments;
  * per block, its fate across all co-optimal assignments:
      fixed to gap j / flexible (several gaps and/or unused) / always
      unused.

Completeness argument (state-compressed search; full assignments are never
enumerated and no result is truncated)
--------------------------------------------------------------------------------
Feasible per-gap subsets (1..6 members, sum inside the band) are bit masks.

* A capped backtracking DFS obtains a feasible incumbent.  Dense instances
  find one almost immediately; it only ever provides pruning bounds.
* Stage 1 state-compressed DP keeps, per used mask, the minimum achievable
  prefix maximum deviation D.  A state with D > incumbent.D is discarded:
  max-deviation is monotone under extension, so it cannot improve D.  The
  final layer minimum is the exact D*.  (Crucially S and U are NOT pruned
  in this stage: a solution with a smaller D may have a larger S.)
* Stage 2 DP keeps only per-gap choices with |dev| <= D* and stores, per
  used mask, the nondominated frontier of (S, U), bounded by the S of a
  feasible assignment that already attains D* (again monotone under
  extension).  Global lexicographic minimum over the final layer gives the
  exact S* and U*.  Equal candidates are never needed for the value, and
  dominance on the same mask is safe for the same monotonicity reason.
* Stage 3 rebuilds exact layers ``used_mask -> {(D,S,U): exact_count}``
  with no dominance merging; a state is kept only while every component is
  <= the optimum, a necessary condition (all contributions are
  non-negative).  Counts are Python ints combined only by multiplication
  over independent choices and addition over disjoint alternatives.
* Block fates are marginal facts joined from the exact prefix and suffix
  layers (bucketed by the residual (S, U); the residual D decides whether
  the suffix must itself attain D*).  No full assignment is materialised.

Feasibility is decided by the DP layers, never by the capped DFS, so the
node cap cannot cause a false "infeasible" result: if the DFS yields no
bound the corresponding stage simply runs unpruned (such instances have
small group tables and merge cheaply).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import Optional


DFS_NODE_CAP = 100_000


@dataclass(frozen=True)
class Block:
    id: int  # 0-based position after sorting by label
    label: str
    length: int


@dataclass(frozen=True)
class Target:
    length: int
    tolerance: int  # allowed absolute deviation, inclusive on both sides


class InfeasibleError(Exception):
    """No assignment satisfies the hard 1..6 block and tolerance bounds."""


# ---------------------------------------------------------------------------
# Feasible subsets for one gap
# ---------------------------------------------------------------------------


def subset_sums(lengths: list[int]) -> list[int]:
    """Exact subset sum per bit mask (sums[0] == 0)."""
    sums = [0] * (1 << len(lengths))
    for mask in range(1, len(sums)):
        bit = mask & -mask
        sums[mask] = sums[mask ^ bit] + lengths[bit.bit_length() - 1]
    return sums


def feasible_groups(blocks: list[Block], target: Target) -> dict[int, int]:
    """Mask -> signed deviation for every feasible subset of one gap."""
    sums = subset_sums([b.length for b in blocks])
    lo = target.length - target.tolerance
    hi = target.length + target.tolerance
    groups: dict[int, int] = {}
    for mask, total in enumerate(sums):
        if 1 <= mask.bit_count() <= 6 and lo <= total <= hi:
            groups[mask] = total - target.length
    return groups


# ---------------------------------------------------------------------------
# Capped backtracking DFS for a feasible incumbent (bound hint only)
# ---------------------------------------------------------------------------


def _candidate_order(groups: dict[int, int], dev_cap: Optional[int]):
    """Per-gap candidates: smallest sets first, then smallest |deviation|."""
    out = []
    for j, g in enumerate(groups):
        items = [
            (abs(d), c.bit_count(), c)
            for c, d in g.items()
            if dev_cap is None or abs(d) <= dev_cap
        ]
        items.sort()
        out.append([c for _, _, c in items])
    return out


def dfs_feasible(
    groups: list[dict[int, int]],
    dev_cap: Optional[int] = None,
    node_cap: int = DFS_NODE_CAP,
) -> Optional[list[int]]:
    """One feasible ordered tuple of per-gap masks under |dev| <= dev_cap.

    Returns None if none is found within the node budget (NOT a proof of
    infeasibility)."""
    m = len(groups)
    order = _candidate_order(groups, dev_cap)
    if any(len(cands) == 0 for cands in order):
        return None
    nodes = 0
    chosen: list[int] = []

    def rec(j: int, used: int) -> bool:
        nonlocal nodes
        if j == m:
            return True
        nodes += 1
        if nodes > node_cap:
            return True  # budget exhausted: abort, caller treats as "no hint"
        for cmask in order[j]:
            if cmask & used:
                continue
            chosen.append(cmask)
            if rec(j + 1, used | cmask):
                return True
            chosen.pop()
        return False

    ok = rec(0, 0)
    if ok and nodes <= node_cap and len(chosen) == m:
        return chosen
    return None


def _assignment_key(
    groups: list[dict[int, int]], masks: list[int]
) -> tuple[int, int, int]:
    devs = [abs(groups[j][c]) for j, c in enumerate(masks)]
    return (max(devs), sum(devs), sum(c.bit_count() for c in masks))


# ---------------------------------------------------------------------------
# Stage 1: minimum D
# ---------------------------------------------------------------------------


def _min_d_layers(
    groups: list[dict[int, int]], d_bound: Optional[int]
) -> list[dict[int, int]]:
    """layers[j]: used_mask -> minimum prefix max-deviation.

    Candidates with |dev| > d_bound are skipped outright: the prefix max
    can never decrease, so such a choice cannot beat the incumbent.
    """
    filtered = [
        {c: d for c, d in g.items() if d_bound is None or abs(d) <= d_bound}
        for g in groups
    ]
    layers: list[dict[int, int]] = [{0: 0}]
    for g in filtered:
        prev = layers[-1]
        cur: dict[int, int] = {}
        for used, d0 in prev.items():
            for cmask, dev in g.items():
                if cmask & used:
                    continue
                a = abs(dev)
                d = d0 if d0 >= a else a
                nm = used | cmask
                old = cur.get(nm)
                if old is None or d < old:
                    cur[nm] = d
        layers.append(cur)
    return layers


# ---------------------------------------------------------------------------
# Stage 2/3: exact key -> count layers bounded by known component caps
# ---------------------------------------------------------------------------


# Exact layer keys are plain triples (D, S, U): tuple hashing/equality is
# implemented in C and dominates the merge hot loop.
Key = tuple  # (max_dev, sum_dev, used)


ExactLayer = dict[int, dict[tuple[int, int, int], int]]


def _min_su_layers(
    tight: list[dict[int, int]], s_bound: Optional[int]
) -> list[dict[int, list[tuple[int, int]]]]:
    """used_mask -> nondominated list of (sum_dev, used); counts ignored."""
    layers: list[dict[int, list[tuple[int, int]]]] = [{0: [(0, 0)]}]

    def add(recs, s, u):
        for s0, u0 in recs:
            if s0 <= s and u0 <= u:
                return
        recs[:] = [(s0, u0) for s0, u0 in recs if not (s <= s0 and u <= u0)]
        recs.append((s, u))

    for g in tight:
        prev = layers[-1]
        cur: dict[int, list[tuple[int, int]]] = {}
        for used, recs in prev.items():
            for cmask, dev in g.items():
                if cmask & used:
                    continue
                a = abs(dev)
                size = cmask.bit_count()
                bucket = cur.setdefault(used | cmask, [])
                for s0, u0 in recs:
                    s, u = s0 + a, u0 + size
                    if s_bound is not None and s > s_bound:
                        continue
                    add(bucket, s, u)
        layers.append(cur)
    return layers


def _exact_merge(
    prev: ExactLayer,
    groups: dict[int, int],
    d_cap: int,
    s_cap: Optional[int],
    u_cap: Optional[int],
) -> ExactLayer:
    out: ExactLayer = {}
    for used_mask, key_counts in prev.items():
        for cmask, dev in groups.items():
            if used_mask & cmask:
                continue
            a = abs(dev)
            if a > d_cap:
                continue
            size = cmask.bit_count()
            bucket = out.setdefault(used_mask | cmask, {})
            for (d0, s0, u0), count in key_counts.items():
                nd = d0 if d0 >= a else a
                ns = s0 + a
                nu = u0 + size
                # Necessary monotone conditions for reaching an optimum no
                # worse than the incumbent: every component contribution is
                # non-negative, so a component above its cap can never be
                # repaired by any completion.
                if nd > d_cap:
                    continue
                if s_cap is not None and ns > s_cap:
                    continue
                if u_cap is not None and nu > u_cap:
                    continue
                nk = (nd, ns, nu)
                bucket[nk] = bucket.get(nk, 0) + count
    return out


def _exact_pipeline(
    groups: list[dict[int, int]],
    d_cap: int,
    s_cap: Optional[int] = None,
    u_cap: Optional[int] = None,
) -> list[ExactLayer]:
    layers: list[ExactLayer] = [{0: {(0, 0, 0): 1}}]
    for g in groups:
        layers.append(_exact_merge(layers[-1], g, d_cap, s_cap, u_cap))
    return layers


def _exact_suffix(
    groups: list[dict[int, int]], opt: tuple
) -> dict[int, ExactLayer]:
    m = len(groups)
    suffix: dict[int, ExactLayer] = {m: {0: {(0, 0, 0): 1}}}
    for j in range(m - 1, -1, -1):
        suffix[j] = _exact_merge(
            suffix[j + 1], groups[j],
            opt[0], opt[1], opt[2])
    return suffix


def _iter_bits(mask: int):
    while mask:
        b = mask & -mask
        yield b.bit_length() - 1
        mask ^= b


# ---------------------------------------------------------------------------
# Main solver
# ---------------------------------------------------------------------------


def solve(blocks: list[Block], targets: list[Target]) -> dict:
    n = len(blocks)
    m = len(targets)
    full = (1 << n) - 1

    groups = [feasible_groups(blocks, t) for t in targets]
    if any(not g for g in groups):
        raise InfeasibleError("at least one gap admits no feasible subset")

    # ---- incumbent hint --------------------------------------------------
    hint = dfs_feasible(groups, dev_cap=None)
    d_bound = _assignment_key(groups, hint)[0] if hint is not None else None

    # ---- stage 1: D* -----------------------------------------------------
    d_layers = _min_d_layers(groups, d_bound)
    final_d = [d for d in d_layers[m].values()]
    if not final_d:
        raise InfeasibleError("gaps cannot be served by disjoint block sets")
    d_star = min(final_d)

    # From here on, only choices with |dev| <= D* can participate: the
    # prefix maximum is non-decreasing.  In dense instances this tight
    # table is a small fraction of the feasible-subset table.
    tight = [
        {c: d for c, d in g.items() if abs(d) <= d_star} for g in groups
    ]

    # ---- S bound from a feasible assignment already attaining D* ---------
    hint_d = dfs_feasible(groups, dev_cap=d_star)
    s_bound = (
        _assignment_key(groups, hint_d)[1] if hint_d is not None else None
    )

    # ---- stage 2: S*, U* via lightweight Pareto DP ----------------------
    # Same-mask dominance on (S, U) is safe for the value: every
    # continuation adds non-negative deltas to both components, so a
    # dominated prefix cannot yield a smaller (S, U).  Counts are not
    # needed here; stage 3 recomputes exact keys/counts.
    su_layers = _min_su_layers(tight, s_bound)
    best_su: Optional[tuple[int, int]] = None
    for recs in su_layers[m].values():
        for s, u in recs:
            if best_su is None or (s, u) < best_su:
                best_su = (s, u)
    assert best_su is not None
    s_star, u_star = best_su
    opt = (d_star, s_star, u_star)

    # ---- stage 3: exact counts / marginals / canonical ------------------
    prefix = _exact_pipeline(tight, d_star, s_star, u_star)
    suffix = _exact_suffix(tight, opt)

    total_count = sum(counts.get(opt, 0) for counts in prefix[m].values())

    # ---- marginal: does block `bit` ever serve gap j? -------------------
    possible = [[False] * n for _ in range(m)]

    for j in range(m):
        fmap = prefix[j]
        smap = suffix[j + 1]
        # Bucket suffix states by exact (S, U).  A disjoint-existence query
        # ("is there a suffix mask disjoint from forbidden mask w?") is
        # answered by a lazily built subset-zeta table: zeta[X] is true iff
        # the bucket contains some suffix mask r with r subseteq X, so the
        # query is zeta[full ^ w] in O(1).  Two tables per bucket: any row,
        # and rows whose suffix key itself attains D* ("hot").
        bucket_rows: dict[tuple[int, int], list[int]] = {}
        bucket_hot: dict[tuple[int, int], list[int]] = {}
        for bmask, key_counts in smap.items():
            per_su: dict[tuple[int, int], bool] = {}
            for (kd, ks, ku) in key_counts:
                per_su[(ks, ku)] = (
                    per_su.get((ks, ku), False) or kd == d_star
                )
            for su, hot in per_su.items():
                bucket_rows.setdefault(su, []).append(bmask)
                if hot:
                    bucket_hot.setdefault(su, []).append(bmask)
        zeta_cache: dict[tuple, bytearray] = {}

        def has_disjoint(su, rows, forbidden: int) -> bool:
            if len(rows) <= 32:
                return any((r & forbidden) == 0 for r in rows)
            z = zeta_cache.get((su, id(rows)))
            if z is None:
                z = bytearray(1 << n)
                for r in rows:
                    z[r] = 1
                for bit in range(n):
                    step = 1 << bit
                    block = step << 1
                    for base in range(0, 1 << n, block):
                        for x in range(base + step, base + block):
                            if z[x - step]:
                                z[x] = 1
                zeta_cache[(su, id(rows))] = z
            return z[full ^ forbidden] == 1

        for cmask, cdev in tight[j].items():
            a = abs(cdev)
            size = cmask.bit_count()
            choice_hot = a == d_star
            for fmask, fkeys in fmap.items():
                if fmask & cmask:
                    continue
                forbidden = fmask | cmask
                for (fd, fs, fu) in fkeys:
                    r_s = s_star - a - fs
                    r_u = u_star - size - fu
                    if r_s < 0 or r_u < 0:
                        continue
                    su = (r_s, r_u)
                    rows = bucket_rows.get(su)
                    if not rows:
                        continue
                    if choice_hot or fd == d_star:
                        ok_join = has_disjoint(su, rows, forbidden)
                    else:
                        hot_rows = bucket_hot.get(su)
                        if not hot_rows:
                            continue
                        ok_join = has_disjoint(su, hot_rows, forbidden)
                    if ok_join:
                        for bit in _iter_bits(cmask):
                            possible[j][bit] = True
                        break  # cmask's participation proven for this gap

    # ---- marginal: can block `bit` be unused? ---------------------------
    optimal_masks = [
        mask for mask, counts in prefix[m].items() if opt in counts
    ]
    can_be_unused = [
        any(not ((mask >> bit) & 1) for mask in optimal_masks)
        for bit in range(n)
    ]

    # ---- classify -------------------------------------------------------
    statuses: list[dict] = []
    for bit in range(n):
        homes = [j for j in range(m) if possible[j][bit]]
        if not homes:
            statuses.append(
                {"block_index": bit, "kind": "unused", "gap": None,
                 "gaps": [], "can_be_unused": True})
        elif len(homes) == 1 and not can_be_unused[bit]:
            statuses.append(
                {"block_index": bit, "kind": "fixed", "gap": homes[0],
                 "gaps": homes, "can_be_unused": False})
        else:
            statuses.append(
                {"block_index": bit, "kind": "flexible", "gap": None,
                 "gaps": homes, "can_be_unused": can_be_unused[bit]})

    # ---- canonical co-optimal assignment --------------------------------
    chosen = _canonical(tight, suffix, opt)
    used_mask = reduce(lambda a, b: a | b, chosen, 0)
    unused = list(_iter_bits(full ^ used_mask))

    return {
        "objective": {
            "max_abs_dev": d_star,
            "sum_abs_dev": s_star,
            "used_blocks": u_star,
        },
        "cooptimal_count": total_count,
        "assignment": [list(_iter_bits(c)) for c in chosen],
        "deviations": [tight[j][chosen[j]] for j in range(m)],
        "unused": unused,
        "optimal_masks": optimal_masks,
        "statuses": statuses,
    }


def _completable(
    suffix: dict[int, ExactLayer],
    j: int,
    used: int,
    key: tuple,
    opt: tuple,
) -> bool:
    """Gaps j..m-1 can extend exact state (used, key) to a full optimum."""
    d0, s0, u0 = key
    od, os_, ou = opt
    for smask, key_counts in suffix[j].items():
        if smask & used:
            continue
        for sd, ss, su in key_counts:
            if (
                max(d0, sd) == od
                and s0 + ss == os_
                and u0 + su == ou
            ):
                return True
    return False


def _canonical(
    groups: list[dict[int, int]],
    suffix: dict[int, ExactLayer],
    opt: tuple,
) -> list[int]:
    """Lexicographically smallest co-optimal tuple of per-gap masks.

    Order: gap 0's ascending id array, then gap 1's, ..., then the unused
    id array.  Greedily take the smallest candidate for each gap that
    leaves a co-optimal completion; the unused array is then fixed and
    therefore minimised automatically."""
    m = len(groups)
    chosen: list[int] = []
    used = 0
    key = (0, 0, 0)
    for j in range(m):
        pick = None
        for cmask in sorted(groups[j], key=lambda c: tuple(_iter_bits(c))):
            if cmask & used:
                continue
            a = abs(groups[j][cmask])
            nk = (
                max(key[0], a),
                key[1] + a,
                key[2] + cmask.bit_count(),
            )
            if not (
                nk[0] <= opt[0] and nk[1] <= opt[1] and nk[2] <= opt[2]
            ):
                continue
            if _completable(suffix, j + 1, used | cmask, nk, opt):
                pick = cmask
                break
        assert pick is not None, "optimum must be achievable"
        chosen.append(pick)
        used |= pick
        a = abs(groups[j][pick])
        key = (max(key[0], a), key[1] + a, key[2] + pick.bit_count())
    return chosen
