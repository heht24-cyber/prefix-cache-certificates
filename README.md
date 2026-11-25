# Prefix Cache Certificates

How much prefill reuse can an eviction policy possibly recover on a fixed LLM request trace? This benchmark produces a replayable cache plan and an integer upper bound on the hit count. A separate verifier checks that the plan is feasible and both numbers coincide, without calling the eviction policy or running an optimizer. The implementation uses Python's standard library.

## Experiment

We replayed all 12,031 requests in the [Mooncake FAST'25 conversation trace](https://github.com/kvcache-ai/Mooncake/blob/3cca71daccf2a7afb8fe3f0295358f70e3a69fdb/FAST25-release/traces/conversation_trace.jsonl). The trace contains 276,491 full 512-token input-block accesses after dropping partial tails. The cache starts empty. Every column below is a count of full-block hits on the same ordered requests.

| Persistent cache blocks | LRU | Next 16 requests | Next 64 | Next 256 | Next 1,024 | Certified optimum |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 12,030 | 12,060 | 12,817 | 13,648 | 13,648 | 13,648 |
| 64 | 12,038 | 12,154 | 13,767 | 17,752 | 17,752 | 17,752 |
| 256 | 12,099 | 12,242 | 14,583 | 28,631 | 28,985 | 28,985 |
| 1,024 | 13,034 | 13,764 | 17,150 | 41,812 | 55,577 | 55,577 |

At 256 blocks, knowing the next 16 requests recovers 143 of the 16,886 hits available above LRU (0.85%). A 256-request preview recovers 16,532 (97.90%). These are perfect-lookahead information budgets. A real predictor must contend with forecast errors. The attainable hit rate even with complete foresight at this capacity is 28,985 / 276,491 = 10.48%.

## Reproduce or verify a certificate

Python 3.10+. The Git blob ID of the input is `5a371794b81d7fc7487ec8a02a2c631bcc7fbdf8`.

```sh
curl -fL -o conversation.jsonl https://raw.githubusercontent.com/kvcache-ai/Mooncake/3cca71daccf2a7afb8fe3f0295358f70e3a69fdb/FAST25-release/traces/conversation_trace.jsonl
python cacheproof.py --self-test
python cacheproof.py conversation.jsonl --capacity 256 --horizons 0 16 64 256 1024 --certificate kvproof-256.json
python cacheproof.py conversation.jsonl --verify kvproof-256.json
```

The last command reconstructs reuse intervals from the input. It reads the capacity, hit depth per request and price sequence from the certificate. The expected output is `Verified optimum: 28985 block hits`. The self-test independently enumerates every legal cache transition on 160 short traces at four capacities, including capacity zero.

## The cache model

Requests execute in file order. A block represents a unique full prefix, including its ancestors. A hit requires the corresponding chain to be resident at request arrival. After serving each request, the policy can keep any prefix-closed subset of the old cache plus the current input path, up to the capacity. Admission is optional. The capacity applies between requests and excludes transient workspace used to serve the current request. Each saved full block has unit value. The experiment omits output KV, concurrent scheduling, transfer costs, partial-block reuse and wall-clock latency.

For horizon H, rank resident and newly available blocks by earliest known next use within H requests. Break ties toward shallower ancestors. For blocks with no visible reuse, prefer the most recently accessed block, then its ancestors. H = 0 gives LRU. The offline implementation indexes the trace for efficient replay. The finite-horizon priority masks uses beyond its information budget. Ancestors are touched whenever a descendant is touched, so they always have at least as high a priority as their descendants.

## How the proof works

Each possible hit corresponds to holding one block continuously between consecutive uses s and e. This consumes one slot after each request in the half-open interval [s,e). Cold uses have no such interval. Relaxing prefix closure gives a maximum-cardinality interval-packing problem with capacity C on every gap. Keeping the intervals with the earliest end times when capacity is exceeded is the classical exchange rule for unit-value intervals. Ancestor-first ties keep the resulting policy feasible in the original tree.

The verifier does not rely on this rule. For any nondecreasing cumulative price P, assign each gap the nonnegative price P[t+1] − P[t]. An interval gets an additional credit max(0, 1 − (P[e] − P[s])). Its gap prices plus credit cover the value of one hit. Summing over selected intervals and charging each gap at most C times yields a universal upper bound:

```text
C × (P[last] − P[0]) + sum over every interval of max(0, 1 − (P[e] − P[s]))
```

For the published 256-block experiment, the first term is 12,800 and the second is 16,185. The verifier also reconstructs the claimed intervals from each request's hit depth, checks that each block is available, and checks capacity and ancestor retention after every request. The resulting feasible plan earns exactly 28,985. Equality certifies optimality even if the generator is replaced by a different implementation.

To construct prices, the generator enforces a price increment of zero across slack gaps, total price at least one on unselected intervals, and at most one on selected intervals. These are difference constraints on request boundaries. The verifier only needs the final integer prices.

## Context

Prior [prefix-cache Belady analysis](https://superattention.github.io/llm-prefix-cache-analysis/) also compares eviction policies against an offline oracle. This repository adds per-trace optimality certificates and measures the value of bounded, perfect foresight on a fixed production-derived trace. The original [Mooncake release](https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release) supplies the anonymized inputs.
