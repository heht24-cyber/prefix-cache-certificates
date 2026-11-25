"""Certified prefix-cache replay. Python 3.10+, standard library only."""
import argparse
from collections import defaultdict, deque
from functools import lru_cache
from itertools import combinations
import json
from pathlib import Path
import random


def read_trace(path):
    requests = []
    with path.open() as stream:
        for line in stream:
            r = json.loads(line)
            n = r["input_length"]
            ids = r["hash_ids"]
            if n < 0 or len(ids) != (n + 511) // 512:
                raise ValueError("expected one hash per 512-token block, including the tail")
            requests.append(tuple(ids[:n // 512]))
    return requests


def index(requests):
    parent, depth, previous, intervals = {}, {}, {}, []
    for t, path in enumerate(requests):
        for d, block in enumerate(path):
            pred = path[d - 1] if d else None
            if parent.setdefault(block, pred) != pred or depth.setdefault(block, d) != d:
                raise ValueError("hash is not a unique prefix")
            if block in previous:
                intervals.append((previous[block], t, block, d))
            previous[block] = t
    return parent, depth, intervals


def replay(requests, depth, capacity, horizon):
    usages = defaultdict(deque)
    for t, path in enumerate(requests):
        for block in path:
            usages[block].append(t)
    cache, last, hits = set(), {}, []
    for t, path in enumerate(requests):
        hits.append(sum(block in cache for block in path))
        for block in path:
            usages[block].popleft()
            last[block] = t
        cache.update(path)
        if len(cache) > capacity:
            def priority(block):
                future = usages[block]
                if future and (horizon is None or future[0] <= t + horizon):
                    return (0, future[0], depth[block], block)
                return (1, -last[block], depth[block], block)
            cache = set(sorted(cache, key=priority)[:capacity])
    return hits


def occupancy(n, selected):
    change = [0] * (n + 1)
    for start, end, *_ in selected:
        change[start] += 1
        change[end] -= 1
    active = 0
    for delta in change[:n]:
        active += delta
        yield active


def certificate(requests, intervals, hits, capacity):
    n = len(requests)
    edges = [[] for _ in range(n + 1)]
    selected = []
    for start, end, block, d in intervals:
        if d < hits[end]:
            selected.append((start, end, block, d))
            edges[end].append((start, -1))
        else:
            edges[start].append((end, 1))
    for t, used in enumerate(occupancy(n, selected)):
        edges[t].append((t + 1, 0))
        if used < capacity:
            edges[t + 1].append((t, 0))
    # Find integral cumulative prices satisfying complementary slackness.
    price = [0] * (n + 1)
    queue = deque(range(n + 1))
    queued = set(queue)
    while queue:
        a = queue.popleft()
        queued.remove(a)
        for b, delta in edges[a]:
            if price[b] < price[a] + delta:
                price[b] = price[a] + delta
                if price[b] > n + 1:
                    raise ValueError("no optimality certificate for supplied plan")
                if b not in queued:
                    queue.append(b)
                    queued.add(b)
    return {"capacity": capacity, "hit_depths": hits, "cumulative_prices": price}


def verify(requests, parent, intervals, cert):
    """Check the witness using only integer arithmetic and the input trace."""
    cap, hits, price = (cert[k] for k in ("capacity", "hit_depths", "cumulative_prices"))
    n = len(requests)
    if type(cap) is not int or cap < 0 or len(hits) != n or len(price) != n + 1:
        raise ValueError("invalid dimensions or capacity")
    if any(type(x) is not int for x in (*price, *hits)):
        raise ValueError("prices and depths must be integers")
    if any(y < x for x, y in zip(price, price[1:])):
        raise ValueError("negative interval price")
    starts = defaultdict(list)
    ends = defaultdict(list)
    upper = cap * (price[-1] - price[0])
    for s, t, b, d in intervals:
        upper += max(0, 1 - (price[t] - price[s]))
        if d < hits[t]:
            starts[s].append(b)
            ends[t].append(b)
    cache = set()
    for t, path in enumerate(requests):
        if not 0 <= hits[t] <= len(path) or set(path[:hits[t]]) != set(ends[t]):
            raise ValueError("invalid claimed prefix or cold hit")
        if not set(ends[t]) <= cache:
            raise ValueError("claimed prefix unavailable")
        cache.difference_update(ends[t])
        cache.update(starts[t])
        if len(cache) > cap or any(parent[b] is not None and parent[b] not in cache for b in cache):
            raise ValueError("capacity or prefix closure violated")
    if sum(hits) != upper:
        raise ValueError("primal and dual objectives differ")
    return upper


def brute(requests, parent, capacity):
    @lru_cache(None)
    def visit(t, saved):
        if t == len(requests):
            return 0
        current = set(requests[t])
        pool = sorted(current.union(saved))
        best = 0
        for size in range(min(len(pool), capacity) + 1):
            for keep in combinations(pool, size):
                if all(parent[b] is None or parent[b] in keep for b in keep):
                    best = max(best, visit(t + 1, keep))
        return len(current.intersection(saved)) + best
    return visit(0, ())


def self_test():
    rng = random.Random(42)
    paths = [(), (0,), (0, 1), (0, 1, 2), (0, 3), (4,), (4, 5)]
    for _ in range(160):
        req = [rng.choice(paths) for _ in range(7)]
        p, d, intervals = index(req)
        for cap in range(4):
            hits = replay(req, d, cap, None)
            cert = certificate(req, intervals, hits, cap)
            assert verify(req, p, intervals, cert) == brute(req, p, cap)
    print("640 exact comparisons passed")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("trace", nargs="?", type=Path)
    cli.add_argument("--capacity", type=int, default=64)
    cli.add_argument("--horizons", type=int, nargs="+", default=[0, 1, 4, 16, 64])
    cli.add_argument("--certificate", type=Path)
    cli.add_argument("--verify", type=Path)
    cli.add_argument("--self-test", action="store_true")
    a = cli.parse_args()
    if a.self_test:
        return self_test()
    if a.trace is None or a.capacity < 0 or min(a.horizons) < 0:
        cli.error("supply a trace and nonnegative capacity and horizons")
    req = read_trace(a.trace)
    parent, depth, intervals = index(req)
    if a.verify:
        value = verify(req, parent, intervals, json.loads(a.verify.read_text()))
        print(f"Verified optimum: {value} block hits")
        return
    hits = replay(req, depth, a.capacity, None)
    proof = certificate(req, intervals, hits, a.capacity)
    value = verify(req, parent, intervals, proof)
    if a.certificate:
        a.certificate.write_text(json.dumps(proof), encoding="utf-8")
    online = {h: sum(replay(req, depth, a.capacity, h)) for h in a.horizons}
    print(json.dumps(dict(requests=len(req), blocks=sum(map(len, req)), capacity=a.capacity,
                          optimal_hits=value, horizon_hits=online)))


if __name__ == "__main__":
    main()
