"""Brute-force ground truth for every candidate problem.

Each function computes the answer by exhaustive enumeration or exact
simulation (no closed-form shortcuts), so the answer key does not depend
on the same reasoning the models are asked to do.
"""
from fractions import Fraction
from itertools import combinations, permutations, product
from math import gcd, isqrt
import json, sys


# P01 — weighted perfect matchings of K8 (Krenn–Gu flavour: amplitude = sum over PMs)
def p01():
    def w(i, j):
        i, j = min(i, j), max(i, j)
        return (j - i) if (i + j) % 2 == 1 else -1

    def pms(vs):
        if not vs:
            yield []
            return
        a = vs[0]
        for k in range(1, len(vs)):
            b = vs[k]
            rest = vs[1:k] + vs[k + 1:]
            for m in pms(rest):
                yield [(a, b)] + m

    total, count = 0, 0
    for m in pms(list(range(1, 9))):
        prod = 1
        for a, b in m:
            prod *= w(a, b)
        total += prod
        count += 1
    assert count == 105
    return total


# P02 — Vieta jumping: ordered pairs (a,b), 1<=a,b<=2026, (ab+1) | (a^2+b^2)
def p02():
    N = 2026
    c = 0
    for a in range(1, N + 1):
        for b in range(1, N + 1):
            if (a * a + b * b) % (a * b + 1) == 0:
                c += 1
    return c


# P03 — triangles of positive area with vertices in the 6x6 lattice grid
def p03():
    pts = [(x, y) for x in range(6) for y in range(6)]
    c = 0
    for p, q, r in combinations(pts, 3):
        if (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]) != 0:
            c += 1
    return c


# P04 — Penney race HTHHT vs THTHH: P(HTHHT first) = m/n, answer m+n
def p04():
    A, B = "HTHHT", "THTHH"
    # exact absorbing Markov chain on the longest suffix that is a prefix of A or B
    states = sorted({A[:i] for i in range(len(A))} | {B[:i] for i in range(len(B))}, key=len)

    def nxt(s, c):
        t = s + c
        if t.endswith(A):
            return "A"
        if t.endswith(B):
            return "B"
        for k in range(len(t), -1, -1):
            suf = t[len(t) - k:]
            if suf in states:
                return suf
        return ""

    # solve p(s) = 1/2 p(nxt(s,H)) + 1/2 p(nxt(s,T)) by Gaussian elimination over Fractions
    idx = {s: i for i, s in enumerate(states)}
    n = len(states)
    M = [[Fraction(0)] * (n + 1) for _ in range(n)]
    for s in states:
        i = idx[s]
        M[i][i] += 1
        for c in "HT":
            t = nxt(s, c)
            if t == "A":
                M[i][n] += Fraction(1, 2)
            elif t == "B":
                pass
            else:
                M[i][idx[t]] -= Fraction(1, 2)
    sol = gauss(M)
    p = sol[idx[""]]
    return p.numerator + p.denominator, str(p)


def gauss(M):
    n = len(M)
    M = [row[:] for row in M]
    for col in range(n):
        piv = next(r for r in range(col, n) if M[r][col] != 0)
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                f = M[r][col]
                M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


# P05 — random walk on the 3x3x3 grid graph, corner -> centre, expected steps m/n -> m+n
def p05():
    cells = list(product(range(3), repeat=3))
    target = (1, 1, 1)
    others = [c for c in cells if c != target]
    idx = {c: i for i, c in enumerate(others)}
    n = len(others)
    M = [[Fraction(0)] * (n + 1) for _ in range(n)]
    for c in others:
        i = idx[c]
        nbrs = []
        for d in range(3):
            for s in (-1, 1):
                v = list(c)
                v[d] += s
                if 0 <= v[d] <= 2:
                    nbrs.append(tuple(v))
        M[i][i] += 1
        M[i][n] += 1
        for v in nbrs:
            if v != target:
                M[i][idx[v]] -= Fraction(1, len(nbrs))
    sol = gauss(M)
    e = sol[idx[(0, 0, 0)]]
    return e.numerator + e.denominator, str(e)


# P06 — bracelets: 12 beads, 3 colours, each used exactly 4 times, up to rotation+reflection
def p06():
    seen = set()
    count = 0
    base = [0] * 4 + [1] * 4 + [2] * 4
    for arr in set(permutations(base)):
        if arr in seen:
            continue
        count += 1
        orbit = set()
        for r in range(12):
            rot = arr[r:] + arr[:r]
            orbit.add(rot)
            orbit.add(rot[::-1])
        seen |= orbit
    return count


# P07 — subsets of {1..20} (incl. empty) with sum divisible by 7
def p07():
    dp = [0] * 7
    dp[0] = 1
    for k in range(1, 21):
        nd = dp[:]
        for r in range(7):
            nd[(r + k) % 7] += dp[r]
        dp = nd
    # cross-check by direct enumeration
    c = sum(1 for mask in range(1 << 20) if sum(i + 1 for i in range(20) if mask >> i & 1) % 7 == 0)
    assert c == dp[0]
    return dp[0]


# P08 — derangements of {1..11} with |pi(i)-i| <= 2 for all i
def p08():
    n = 11
    count = 0

    def dfs(i, used):
        nonlocal count
        if i > n:
            count += 1
            return
        for v in range(max(1, i - 2), min(n, i + 2) + 1):
            if v != i and not (used >> v) & 1:
                dfs(i + 1, used | (1 << v))

    dfs(1, 0)
    return count


# P09 — subtraction game {2,3,7}: number of losing n in 1..2026 for the player to move
def p09():
    N = 2026
    win = [False] * (N + 1)
    for n in range(N + 1):
        win[n] = any(n >= m and not win[n - m] for m in (2, 3, 7))
    return sum(1 for n in range(1, N + 1) if not win[n])


# P10 — a1=1, a_{n+1}=a_n+floor(sqrt(a_n)); index n with a_n = 2^20
def p10():
    a, n = 1, 1
    target = 2 ** 20
    while a < target:
        a += isqrt(a)
        n += 1
    assert a == target, (a, target)
    return n


# P11 — tilings of a 3x12 board by 1x2 dominoes (either orientation) and 2x2 squares
def p11():
    R, C = 3, 12
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def count(filled):
        # filled: frozenset bitmask over R*C cells, cell (r,c) -> c*R + r
        if filled == (1 << (R * C)) - 1:
            return 1
        # first empty cell in column-major order
        k = 0
        while (filled >> k) & 1:
            k += 1
        c, r = divmod(k, R)
        total = 0
        # horizontal domino (r,c),(r,c+1)
        if c + 1 < C and not (filled >> ((c + 1) * R + r)) & 1:
            total += count(filled | (1 << k) | (1 << ((c + 1) * R + r)))
        # vertical domino (r,c),(r+1,c)
        if r + 1 < R and not (filled >> (k + 1)) & 1:
            total += count(filled | (1 << k) | (1 << (k + 1)))
        # 2x2 square with top-left (r,c)
        if r + 1 < R and c + 1 < C:
            cells = [k, k + 1, (c + 1) * R + r, (c + 1) * R + r + 1]
            if all(not (filled >> x) & 1 for x in cells):
                m = filled
                for x in cells:
                    m |= 1 << x
                total += count(m)
        return total

    return count(0)


# P12 — Josephus: n=2026 people in a circle numbered 1..2026, every 3rd eliminated
# (counting starts at person 1, so person 3 is eliminated first); survivor's number
def p12():
    people = list(range(1, 2027))
    i = 0
    while len(people) > 1:
        i = (i + 2) % len(people)
        people.pop(i)
    return people[0]


# P13 — lattice paths (0,0)->(12,12), unit steps E/N, always |x-y| <= 2
def p13():
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def f(x, y):
        if abs(x - y) > 2 or x > 12 or y > 12:
            return 0
        if (x, y) == (12, 12):
            return 1
        return f(x + 1, y) + f(x, y + 1)

    return f(0, 0)


if __name__ == "__main__":
    out = {}
    for name in sorted(k for k in globals() if k.startswith("p") and k[1:].isdigit()):
        v = globals()[name]()
        out[name] = v
        print(name, v, flush=True)
    json.dump({k: (v if isinstance(v, int) else list(v)) for k, v in out.items()}, open("answers_bruteforce.json", "w"), indent=1)


# P14 — sum_{k=1}^{2028} floor(k^2 / 2029)   (2029 is prime, 2029 = 1 mod 4)
def p14():
    p = 2029
    assert all(p % d for d in range(2, isqrt(p) + 1))
    return sum(k * k // p for k in range(1, p))


# P15 — proper 3-colourings of the vertices of the 4x4 grid graph
def p15():
    # (a) row transfer matrix
    rows = [r for r in product(range(3), repeat=4) if all(r[i] != r[i + 1] for i in range(3))]
    vec = {r: 1 for r in rows}
    for _ in range(3):
        vec = {r2: sum(v for r1, v in vec.items() if all(a != b for a, b in zip(r1, r2))) for r2 in rows}
    tm = sum(vec.values())
    # (b) independent cell-by-cell DFS
    n = 4
    grid = [[-1] * n for _ in range(n)]
    cnt = 0

    def dfs(k):
        nonlocal cnt
        if k == n * n:
            cnt += 1
            return
        r, c = divmod(k, n)
        for col in range(3):
            if r and grid[r - 1][c] == col:
                continue
            if c and grid[r][c - 1] == col:
                continue
            grid[r][c] = col
            dfs(k + 1)
        grid[r][c] = -1

    dfs(0)
    assert cnt == tm, (cnt, tm)
    return tm


def p05b():
    """P05 final form: corner -> opposite corner on the 3x3x3 grid."""
    cells = list(product(range(3), repeat=3))
    target = (2, 2, 2)
    others = [c for c in cells if c != target]
    idx = {c: i for i, c in enumerate(others)}
    n = len(others)
    M = [[Fraction(0)] * (n + 1) for _ in range(n)]
    for c in others:
        i = idx[c]
        nbrs = []
        for d in range(3):
            for s in (-1, 1):
                v = list(c); v[d] += s
                if 0 <= v[d] <= 2:
                    nbrs.append(tuple(v))
        M[i][i] += 1; M[i][n] += 1
        for v in nbrs:
            if v != target:
                M[i][idx[v]] -= Fraction(1, len(nbrs))
    e = gauss(M)[idx[(0, 0, 0)]]
    return e.numerator + e.denominator, str(e)
