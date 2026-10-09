"""Ground truth for the FRONTIER set (v3)."""
from fractions import Fraction
from functools import lru_cache
from itertools import product
from math import isqrt
import sys

sys.setrecursionlimit(100000)


# F01 — random walk on the 5x5x5 grid graph, (0,0,0) -> (4,4,4): E = m/n, answer m+n
def f01():
    from verify_hard import gauss
    N = 5
    cells = list(product(range(N), repeat=3))
    target = (N - 1,) * 3
    others = [c for c in cells if c != target]
    idx = {c: i for i, c in enumerate(others)}
    n = len(others)
    M = [[Fraction(0)] * (n + 1) for _ in range(n)]
    for c in others:
        i = idx[c]
        nb = []
        for d in range(3):
            for s in (-1, 1):
                v = list(c); v[d] += s
                if 0 <= v[d] < N:
                    nb.append(tuple(v))
        M[i][i] += 1; M[i][n] += 1
        for v in nb:
            if v != target:
                M[i][idx[v]] -= Fraction(1, len(nb))
    e = gauss(M)[idx[(0, 0, 0)]]
    return e.numerator + e.denominator, str(e)


# F02 — permutations of {1..15} with 1 <= |p(i)-i| <= 4
def f02():
    n, k = 15, 4
    # exact DP over (position, set of used values in window) — independent of any by-hand state design
    @lru_cache(maxsize=None)
    def go(i, used):  # used: frozenset of values already used that are >= i-k
        if i > n:
            return 1
        # all values < i-k must be used already: enforce that value i-k is used before moving past
        if i - k - 1 >= 1 and (i - k - 1) not in used:
            return 0
        tot = 0
        for v in range(max(1, i - k), min(n, i + k) + 1):
            if v != i and v not in used:
                nu = frozenset(u for u in used | {v} if u >= i + 1 - k - 1)
                tot += go(i + 1, nu)
        return tot
    a = go(1, frozenset())
    # brute-force cross-check with plain DFS
    cnt = 0
    def dfs(i, used):
        nonlocal cnt
        if i > n:
            cnt += 1
            return
        if i - k - 1 >= 1 and not used >> (i - k - 1) & 1:
            return
        for v in range(max(1, i - k), min(n, i + k) + 1):
            if v != i and not used >> v & 1:
                dfs(i + 1, used | 1 << v)
    dfs(1, 0)
    assert a == cnt, (a, cnt)
    return a


# F03 — self-avoiding walks of length 10 on the square lattice starting at the origin and never visiting y < 0
def f03():
    L = 10
    cnt = 0
    def dfs(x, y, steps, seen):
        nonlocal cnt
        if steps == L:
            cnt += 1
            return
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if ny < 0 or (nx, ny) in seen:
                continue
            seen.add((nx, ny))
            dfs(nx, ny, steps + 1, seen)
            seen.remove((nx, ny))
    dfs(0, 0, 0, {(0, 0)})
    return cnt


# F04 — sum of Euler phi(n) for n = 1..2026
def f04():
    N = 2026
    phi = list(range(N + 1))
    for p in range(2, N + 1):
        if phi[p] == p:
            for m in range(p, N + 1, p):
                phi[m] -= phi[m] // p
    s = sum(phi[1:])
    # independent check: count coprime pairs 1<=a<=b<=N with gcd 1
    from math import gcd
    t = sum(1 for b in range(1, N + 1) for a in range(1, b + 1) if gcd(a, b) == 1)
    assert s == t
    return s


if __name__ == "__main__":
    for name in sys.argv[1:] or ["f01", "f02", "f03", "f04"]:
        print(name, globals()[name](), flush=True)
