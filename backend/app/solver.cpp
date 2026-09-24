// Gauge block multi-gap optimal allocator.
//
// Provably complete state-compressed dynamic programming (no enumeration or
// truncation of full assignments; n <= 16, masks fit in uint32).
//
// Every complete solution is an ordered tuple (S_0, ..., S_{j-1}) of pairwise
// disjoint subsets of the n blocks, 1 <= |S_g| <= 6; leftover blocks are
// unused.
//
// The lexicographic objective (M, A, U) combines with a suffix via
// (max(M, M'), A + A', U + U'), so a prefix cannot be pruned on the whole
// triple: a prefix with smaller M but larger A may later be overtaken once a
// suffix forces a larger M.  The search is therefore split in two:
//
//   Pass 1 (minimax).  For every used-mask keep only the smallest achievable
//   prefix max-deviation.  Pruning on M alone is safe because max is monotone;
//   the best final mask gives M*.
//
//   Pass 2 (sum, used count).  Edges are restricted to |deviation| <= M*;
//   every complete solution feasible under this bound then has max deviation
//   exactly M* (minimality of M*), so the remaining objective (A, U) is fully
//   additive and lexicographic per-mask pruning is lossless: a strictly
//   dominated prefix can never be part of an optimal suffix.  Exact co-optimal
//   prefix counts are summed as uint64 (structural bound 5^16 < 2^38).
//
// A symmetric backward table for pass 2 makes every optimal continuation
// countable.  One merge sweep over all transitions counts, for each block, how
// many of ALL optimal solutions send it to each gap or leave it unused.
//
// Input (stdin, plain text):
//   line 1: n j
//   line 2: n identifiers (validated upstream, no whitespace)
//   line 3: n integer lengths (nm)
//   line 4: j integer target gaps
//   line 5: j integer non-negative tolerances
//
// Output: one JSON object on stdout.

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

using namespace std;

using i64 = int64_t;
using u64 = uint64_t;
using Mask = uint32_t;

static constexpr i64 INF = (1LL << 60);

struct Edge {
    Mask s;
    i64 ad;  // |deviation|
};

struct Table {
    vector<i64> sa;  // sum |dev| of co-optimal prefixes/suffixes
    vector<int> uu;  // used blocks
    vector<u64> cnt; // exact number of co-optimal ways
    explicit Table(int nstates)
        : sa(nstates, INF), uu(nstates, -1), cnt(nstates, 0) {}
};

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    int n, j;
    if (!(cin >> n >> j)) return 2;
    vector<string> id(n);
    for (auto &x : id) cin >> x;
    vector<i64> w(n), T(j), tol(j);
    for (auto &x : w) cin >> x;
    for (auto &x : T) cin >> x;
    for (auto &x : tol) cin >> x;

    if (n < 1 || n > 16 || j < 1 || j > 4) {
        cout << "{\"feasible\":false,\"reason\":\"bounds\"}\n";
        return 0;
    }

    const int N = 1 << n;
    const Mask FULL = (Mask)(N - 1);

    vector<int> pc(N, 0);
    for (int m = 1; m < N; ++m) pc[m] = pc[m >> 1] + (m & 1);

    vector<i64> ssum(N, 0);
    for (int m = 1; m < N; ++m) {
        int b = __builtin_ctz((unsigned)m);
        ssum[m] = ssum[m ^ (1 << b)] + w[b];
    }

    // Ascending-identifier order (independent of input order).
    vector<int> ord(n);
    for (int i = 0; i < n; ++i) ord[i] = i;
    sort(ord.begin(), ord.end(), [&](int a, int b) {
        if (id[a] != id[b]) return id[a] < id[b];
        return a < b;
    });
    vector<int> idRank(n);
    for (int r = 0; r < n; ++r) idRank[ord[r]] = r;

    // Lexicographic order on the arrays of member identifiers sorted
    // ascending.  Let x be the smallest identifier where membership differs;
    // all smaller identifiers are common members, occupying the first k slots
    // of both arrays.  At slot k the side containing x has value x; the other
    // side either has ended (it is the strict prefix -> smaller) or places an
    // identifier greater than x (the side with x is smaller).  "Has the other
    // side ended" = it contains no member whose identifier exceeds x.
    vector<Mask> higherRank(n + 1, 0);
    for (int r = n - 1; r >= 0; --r)
        higherRank[r] = higherRank[r + 1] | (1u << ord[r]);
    auto maskLess = [&](Mask a, Mask b) -> bool {
        if (a == b) return false;
        Mask da = a & ~b, db = b & ~a;
        Mask d = da | db;
        int best = n;
        Mask x = d;
        while (x) {
            int bit = __builtin_ctz((unsigned)x);
            if (idRank[bit] < best) best = idRank[bit];
            x &= x - 1;
        }
        int idx = ord[best];
        Mask later = higherRank[best + 1];
        if (da & (1u << idx))
            return (b & later) != 0;  // x in a: a smaller unless b ended
        return (a & later) == 0;      // x in b: a smaller only if a ended
    };

    // ---- feasible subsets per gap (tolerance enforced here) --------------
    // Pass-1 edges: every subset satisfying 1<=|s|<=6 and |dev| <= tolerance.
    vector<vector<Edge>> allEdges(j);
    for (int g = 0; g < j; ++g) {
        for (Mask s = 1; s < (Mask)N; ++s) {
            int k = pc[s];
            if (k < 1 || k > 6) continue;
            i64 d = ssum[s] - T[g];
            if (d < 0) d = -d;
            if (d <= tol[g]) allEdges[g].push_back({s, d});
        }
        if (allEdges[g].empty()) {
            cout << "{\"feasible\":false}\n";
            return 0;
        }
    }

    // ---- pass 1: minimax feasibility + M* ---------------------------------
    // mm[g][m] = smallest achievable max |dev| over prefixes using mask m.
    vector<vector<i64>> mm(j + 1, vector<i64>(N, INF));
    mm[0][0] = 0;
    for (int g = 0; g < j; ++g) {
        for (const Edge &e : allEdges[g]) {
            const Mask s = e.s;
            const Mask comp = FULL ^ s;
            for (Mask p = comp;; p = (p - 1) & comp) {
                if (mm[g][p] != INF) {
                    Mask mp = p | s;
                    i64 v = mm[g][p] >= e.ad ? mm[g][p] : e.ad;
                    if (v < mm[g + 1][mp]) mm[g + 1][mp] = v;
                }
                if (p == 0) break;
            }
        }
        bool reachable = false;
        for (int m = 0; m < N; ++m)
            if (mm[g + 1][m] != INF) { reachable = true; break; }
        if (!reachable) {
            cout << "{\"feasible\":false}\n";
            return 0;
        }
    }
    i64 Mstar = INF;
    for (int m = 0; m < N; ++m)
        if (mm[j][m] < Mstar) Mstar = mm[j][m];

    // ---- pass-2 edges: deviation bounded by M* ----------------------------
    // Every complete assignment on these edges has max deviation exactly M*
    // (no complete assignment has max < M* by minimality), so (A, U) fully
    // decides optimality among them.  Arrays are sorted for canonical choice.
    vector<vector<Edge>> edges(j);
    for (int g = 0; g < j; ++g) {
        i64 bound = Mstar < tol[g] ? Mstar : tol[g];
        for (const Edge &e : allEdges[g])
            if (e.ad <= bound) edges[g].push_back(e);
        sort(edges[g].begin(), edges[g].end(),
             [&](const Edge &a, const Edge &b) { return maskLess(a.s, b.s); });
    }

    // ---- forward DP on (sum |dev|, used count) ----------------------------
    vector<Table> fwd;
    fwd.reserve(j + 1);
    fwd.emplace_back(N);
    fwd[0].sa[0] = 0;
    fwd[0].uu[0] = 0;
    fwd[0].cnt[0] = 1;

    for (int g = 0; g < j; ++g) {
        fwd.emplace_back(N);
        const Table &prev = fwd[g];
        Table &cur = fwd[g + 1];
        for (const Edge &e : edges[g]) {
            const Mask s = e.s;
            const Mask comp = FULL ^ s;
            const int k = pc[s];
            for (Mask p = comp;; p = (p - 1) & comp) {
                if (prev.cnt[p]) {
                    const Mask mp = p | s;
                    i64 sa = prev.sa[p] + e.ad;
                    int uu = prev.uu[p] + k;
                    if (sa < cur.sa[mp] ||
                        (sa == cur.sa[mp] && uu < cur.uu[mp])) {
                        cur.sa[mp] = sa;
                        cur.uu[mp] = uu;
                        cur.cnt[mp] = prev.cnt[p];
                    } else if (sa == cur.sa[mp] && uu == cur.uu[mp]) {
                        cur.cnt[mp] += prev.cnt[p];
                    }
                }
                if (p == 0) break;
            }
        }
        bool reachable = false;
        for (int m = 0; m < N; ++m)
            if (cur.cnt[m]) { reachable = true; break; }
        if (!reachable) {
            cout << "{\"feasible\":false}\n";
            return 0;
        }
    }

    // ---- global optimum over final used masks ----------------------------
    i64 Ksa = INF;
    int Ku = -1;
    u64 total = 0;
    {
        const Table &last = fwd[j];
        for (int m = 0; m < N; ++m) {
            if (!last.cnt[m]) continue;
            if (last.sa[m] < Ksa ||
                (last.sa[m] == Ksa && last.uu[m] < Ku)) {
                Ksa = last.sa[m];
                Ku = last.uu[m];
                total = last.cnt[m];
            } else if (last.sa[m] == Ksa && last.uu[m] == Ku) {
                total += last.cnt[m];
            }
        }
    }

    // ---- backward DP on (sum |dev|, used count) ---------------------------
    vector<Table> bwd;
    bwd.reserve(j + 1);
    for (int x = 0; x <= j; ++x) bwd.emplace_back(N);
    for (int m = 0; m < N; ++m) {
        bwd[j].sa[m] = 0;
        bwd[j].uu[m] = 0;
        bwd[j].cnt[m] = 1;
    }
    for (int g = j - 1; g >= 0; --g) {
        const Table &nxt = bwd[g + 1];
        Table &cur = bwd[g];
        for (const Edge &e : edges[g]) {
            const Mask s = e.s;
            const Mask comp = FULL ^ s;
            const int k = pc[s];
            for (Mask p = comp;; p = (p - 1) & comp) {
                const Mask mp = p | s;
                if (nxt.cnt[mp]) {
                    i64 sa = nxt.sa[mp] + e.ad;
                    int uu = nxt.uu[mp] + k;
                    if (sa < cur.sa[p] ||
                        (sa == cur.sa[p] && uu < cur.uu[p])) {
                        cur.sa[p] = sa;
                        cur.uu[p] = uu;
                        cur.cnt[p] = nxt.cnt[mp];
                    } else if (sa == cur.sa[p] && uu == cur.uu[p]) {
                        cur.cnt[p] += nxt.cnt[mp];
                    }
                }
                if (p == 0) break;
            }
        }
    }

    // ---- block flow across ALL optimal solutions --------------------------
    // A transition (p -> s at gap g) lies on an optimal solution iff the
    // combined (sum, used) equals (Ksa, Ku).  fwdCnt * bwdCnt counts the exact
    // (prefix, suffix) pairings; each is a distinct complete solution since s
    // and g are fixed in the product.
    vector<vector<u64>> flow(j, vector<u64>(n, 0));
    for (int g = 0; g < j; ++g) {
        const Table &fp = fwd[g];
        const Table &bn = bwd[g + 1];
        for (const Edge &e : edges[g]) {
            const Mask s = e.s;
            const Mask comp = FULL ^ s;
            const int k = pc[s];
            for (Mask p = comp;; p = (p - 1) & comp) {
                if (fp.cnt[p] && bn.cnt[p | s]) {
                    const Mask mp = p | s;
                    if (fp.sa[p] + e.ad + bn.sa[mp] == Ksa &&
                        fp.uu[p] + k + bn.uu[mp] == Ku) {
                        // Exact and overflow-safe: complete solutions number
                        // at most 5^16 < 2^38; the largest intermediate raw
                        // product is 3^16 * 3^16 = 3^32 < 2^51 < 2^64.
                        u64 ways = fp.cnt[p] * bn.cnt[mp];
                        Mask x = s;
                        while (x) {
                            int bit = __builtin_ctz((unsigned)x);
                            flow[g][bit] += ways;
                            x &= x - 1;
                        }
                    }
                }
                if (p == 0) break;
            }
        }
    }

    // ---- canonical solution ----------------------------------------------
    // Gap order: smallest ascending-ID member array that still extends to an
    // optimal solution (edges are already sorted that way).  The unused array
    // is then forced, so it needs no further arbitration.
    vector<Mask> canon(j, 0);
    Mask curMask = 0;
    for (int g = 0; g < j; ++g) {
        const Table &fp = fwd[g];
        const Table &bn = bwd[g + 1];
        for (const Edge &e : edges[g]) {
            if (e.s & curMask) continue;
            const Mask mp = curMask | e.s;
            if (!bn.cnt[mp]) continue;
            if (fp.sa[curMask] + e.ad + bn.sa[mp] == Ksa &&
                fp.uu[curMask] + pc[e.s] + bn.uu[mp] == Ku) {
                canon[g] = e.s;
                curMask = mp;
                break;
            }
        }
    }
    Mask usedMask = 0;
    for (Mask s : canon) usedMask |= s;
    Mask unusedMask = FULL ^ usedMask;

    // ---- JSON --------------------------------------------------------------
    auto q = [&](const string &s) {
        string o = "\"";
        for (char c : s) {
            if (c == '"' || c == '\\') o.push_back('\\');
            o.push_back(c);
        }
        o.push_back('"');
        return o;
    };
    auto qn = [&](u64 v) { return q(to_string(v)); };  // exact big count
    auto maskIds = [&](Mask m) {
        vector<int> v;
        Mask x = m;
        while (x) {
            v.push_back(__builtin_ctz((unsigned)x));
            x &= x - 1;
        }
        sort(v.begin(), v.end(),
             [&](int a, int b) { return id[a] < id[b]; });
        string o = "[";
        for (size_t i = 0; i < v.size(); ++i) {
            if (i) o.push_back(',');
            o += q(id[v[i]]);
        }
        o.push_back(']');
        return o;
    };

    cout << "{\"feasible\":true";
    cout << ",\"maxAbsDev\":" << Mstar
         << ",\"sumAbsDev\":" << Ksa
         << ",\"usedCount\":" << Ku
         << ",\"totalOptimal\":" << qn(total);

    cout << ",\"canonical\":[";
    for (int g = 0; g < j; ++g) {
        if (g) cout << ',';
        cout << maskIds(canon[g]);
    }
    cout << ']';

    cout << ",\"gaps\":[";
    for (int g = 0; g < j; ++g) {
        if (g) cout << ',';
        i64 actual = ssum[canon[g]];
        cout << "{\"target\":" << T[g] << ",\"tolerance\":" << tol[g]
             << ",\"actual\":" << actual
             << ",\"deviation\":" << actual - T[g] << "}";
    }
    cout << ']';

    cout << ",\"unused\":" << maskIds(unusedMask);

    cout << ",\"destinations\":[";
    for (int b = 0; b < n; ++b) {
        if (b) cout << ',';
        u64 usedWays = 0;
        int gapCount = 0;
        for (int g = 0; g < j; ++g) {
            usedWays += flow[g][b];
            if (flow[g][b]) ++gapCount;
        }
        u64 unusedWays = total - usedWays;

        struct Dst { int g; u64 c; };
        vector<Dst> dsts;
        for (int g = 0; g < j; ++g)
            if (flow[g][b]) dsts.push_back({g, flow[g][b]});
        if (unusedWays) dsts.push_back({-1, unusedWays});

        string kind;
        int fixedGap = -1;
        if (dsts.size() == 1) {
            if (dsts[0].g == -1) kind = "always_unused";
            else { kind = "fixed"; fixedGap = dsts[0].g; }
        } else kind = "flexible";

        cout << "{\"id\":" << q(id[b]) << ",\"kind\":" << q(kind);
        if (fixedGap >= 0) cout << ",\"gap\":" << fixedGap;
        cout << ",\"flows\":[";
        for (size_t k2 = 0; k2 < dsts.size(); ++k2) {
            if (k2) cout << ',';
            cout << "{\"gap\":" << dsts[k2].g
                 << ",\"count\":" << qn(dsts[k2].c) << "}";
        }
        cout << "]}";
    }
    cout << ']';

    cout << "}\n";
    return 0;
}
