"""Ground truth for the HARD set (v2). Exhaustive / exact methods only."""
from fractions import Fraction
from functools import lru_cache
from itertools import combinations, permutations, product
from math import gcd, isqrt
import random, sys, json

sys.setrecursionlimit(10000)


def tilings(cells):
    """Count domino tilings of a set of (r,c) cells by memoised first-empty-cell DFS."""
    cells = sorted(cells)
    idx = {c: i for i, c in enumerate(cells)}
    full = (1 << len(cells)) - 1

    @lru_cache(maxsize=None)
    def f(mask):
        if mask == full:
            return 0 if False else 1
        i = 0
        while mask >> i & 1:
            i += 1
        r, c = cells[i]
        tot = 0
        for d in ((0, 1), (1, 0)):
            nb = (r + d[0], c + d[1])
            j = idx.get(nb)
            if j is not None and not mask >> j & 1:
                tot += f(mask | 1 << i | 1 << j)
        return tot
    return f(0)


# H01 — domino tilings of the 8x8 board with the central 2x2 removed
def h01():
    cells = [(r, c) for r in range(8) for c in range(8) if not (r in (3, 4) and c in (3, 4))]
    return tilings(cells)


# H02 — proper 3-colourings of the 5x5 grid graph (two methods)
def h02():
    n = 5
    rows = [r for r in product(range(3), repeat=n) if all(r[i] != r[i + 1] for i in range(n - 1))]
    vec = {r: 1 for r in rows}
    for _ in range(n - 1):
        vec = {r2: sum(v for r1, v in vec.items() if all(a != b for a, b in zip(r1, r2))) for r2 in rows}
    a = sum(vec.values())
    # column-by-column independent check with a different traversal (columns instead of rows) — same by symmetry,
    # so do a cell-by-cell profile DP instead
    from collections import defaultdict
    dp = {(): 1}
    for r in range(n):
        for c in range(n):
            nd = defaultdict(int)
            for prof, v in dp.items():
                # prof holds colours of the last n cells in row-major order
                for col in range(3):
                    if c > 0 and prof[-1] == col:
                        continue
                    if r > 0 and prof[-n] == col:
                        continue
                    np_ = (prof + (col,))[-n:]
                    nd[np_] += v
            dp = nd
    b = sum(dp.values())
    assert a == b, (a, b)
    return a


# H03 — domino tilings of a 2x3x4 box (3D, 24 unit cubes)
def h03():
    cells = sorted(product(range(2), range(3), range(4)))
    idx = {c: i for i, c in enumerate(cells)}
    full = (1 << 24) - 1

    @lru_cache(maxsize=None)
    def f(mask):
        if mask == full:
            return 1
        i = 0
        while mask >> i & 1:
            i += 1
        x, y, z = cells[i]
        tot = 0
        for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            j = idx.get((x + d[0], y + d[1], z + d[2]))
            if j is not None and not mask >> j & 1:
                tot += f(mask | 1 << i | 1 << j)
        return tot
    return f(0)


# H04 — hafnian of a 10x10 structured matrix: sum over the 945 perfect matchings of K10
def h04():
    def w(i, j):
        return (i * j) % 7 - 3

    @lru_cache(maxsize=None)
    def haf(vs):
        if not vs:
            return 1
        a, rest = vs[0], vs[1:]
        return sum(w(a, rest[k]) * haf(rest[:k] + rest[k + 1:]) for k in range(len(rest)))
    v = haf(tuple(range(1, 11)))
    # brute force: enumerate all matchings explicitly
    def pms(vs):
        if not vs:
            yield 1
            return
        a = vs[0]
        for k in range(1, len(vs)):
            for m in pms(vs[1:k] + vs[k + 1:]):
                yield w(a, vs[k]) * m
    assert v == sum(pms(list(range(1, 11))))
    return v


# H05 — permutations of {1..13} with 1 <= |p(i)-i| <= 3 for all i
def h05():
    n = 13
    cnt = 0

    def dfs(i, used):
        nonlocal cnt
        if i > n:
            cnt += 1
            return
        for v in range(max(1, i - 3), min(n, i + 3) + 1):
            if v != i and not used >> v & 1:
                dfs(i + 1, used | 1 << v)
    dfs(1, 0)
    return cnt


# H06 — gcd / (lcm/gcd) blackboard (IMO 2026 P1 mechanics, new instance): simulate many random plays
H06_NUMS = [2**6 * 3**9, 2**9 * 5**6, 3**6 * 5**9 * 7**4, 2**15 * 7**6, 3**12 * 7**10]
def h06():
    finals = set()
    rng = random.Random(7)
    for _ in range(300):
        board = H06_NUMS[:]
        while True:
            big = [i for i, x in enumerate(board) if x > 1]
            if len(big) < 2:
                break
            i, j = rng.sample(big, 2)
            m, n = board[i], board[j]
            g = gcd(m, n)
            board[i], board[j] = g, (m * n // g) // g
        rest = [x for x in board if x > 1]
        assert len(rest) == 1
        finals.add(rest[0])
    assert len(finals) == 1, finals
    return finals.pop()


# H07 — IMO 2026 P6 sequence, new instance: a_1 = 35; a_{n+1} = least integer > a_n sharing a factor > 1 with every earlier term
def h07_seq(a1, N):
    seq = [a1]
    while len(seq) < N:
        x = seq[-1] + 1
        while not all(gcd(x, a) > 1 for a in seq):
            x += 1
        seq.append(x)
    return seq

def h07():
    s = h07_seq(35, 2026)
    return s[-1]


# H08 — integer points (x,y,z) with x^2+y^2+z^2 <= 2026
def h08():
    R = 2026
    r = isqrt(R)
    c = 0
    for x in range(-r, r + 1):
        for y in range(-r, r + 1):
            rem = R - x * x - y * y
            if rem >= 0:
                c += 2 * isqrt(rem) + 1
    return c


# H09 — colourings of the 12 edges of a cube, 4 red / 4 green / 4 blue, up to all 48 symmetries
def h09():
    verts = list(product((-1, 1), repeat=3))
    edges = [frozenset((u, v)) for u, v in combinations(verts, 2) if sum(a != b for a, b in zip(u, v)) == 1]
    eidx = {e: i for i, e in enumerate(edges)}
    syms = []
    for perm in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            def g(v, perm=perm, signs=signs):
                return tuple(signs[k] * v[perm[k]] for k in range(3))
            syms.append([eidx[frozenset(g(v) for v in e)] for e in edges])
    assert len(syms) == 48
    seen = set()
    cnt = 0
    for col in set(permutations([0] * 4 + [1] * 4 + [2] * 4)):
        if col in seen:
            continue
        cnt += 1
        for s in syms:
            img = [0] * 12
            for i in range(12):
                img[s[i]] = col[i]
            seen.add(tuple(img))
    return cnt


# H10 — random walk on the 4x4x4 grid graph, corner (0,0,0) -> opposite corner (3,3,3): E = m/n, answer m+n
def gauss(M):
    n = len(M)
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

def h10():
    N = 4
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


# H11 — take-and-break game: from one row remove 1 coin from either END, or remove 3 ADJACENT coins
# from anywhere (which may split the row in two). Last player to move wins. Count n in 1..500
# for which a single row of n coins is a loss for the player to move.
def grundy_seq(N):
    g = [0] * (N + 1)
    for n in range(1, N + 1):
        opts = set()
        opts.add(g[n - 1])  # remove one end coin
        if n >= 3:
            for left in range(0, n - 3 + 1):
                right = n - 3 - left
                opts.add(g[left] ^ g[right])
        m = 0
        while m in opts:
            m += 1
        g[n] = m
    return g

def h11():
    g = grundy_seq(500)
    return sum(1 for n in range(1, 501) if g[n] == 0)


# H12 — bracelets of 15 beads, 3 colours, no two adjacent beads equal, up to rotation and reflection
def h12():
    n = 15
    seen = set()
    cnt = 0
    def gen(prefix):
        if len(prefix) == n:
            if prefix[0] != prefix[-1]:
                yield tuple(prefix)
            return
        for c in range(3):
            if not prefix or prefix[-1] != c:
                prefix.append(c)
                yield from gen(prefix)
                prefix.pop()
    allc = list(gen([]))
    assert len(allc) == 2 ** n - 2
    for col in allc:
        if col in seen:
            continue
        cnt += 1
        for r in range(n):
            rot = col[r:] + col[:r]
            seen.add(rot); seen.add(rot[::-1])
    return cnt


if __name__ == "__main__":
    out = {}
    for name in sys.argv[1:] or sorted(k for k in globals() if k.startswith("h") and k[1:].isdigit()):
        v = globals()[name]()
        out[name] = v
        print(name, v, flush=True)
