"""Boil one or more swarm.py result files down to the numbers a decision needs.

    python loadtest/report.py results-a.json results-b.json ...

For each run: latency percentiles per action, how many students had any
trouble at all (the figure that matters for "safe for ANY student"), the worst
single wait each student sat through, and a per-minute timeline of answer
latency so a slow patch can be lined up with what the class was doing.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def pct(values, q):
    values = sorted(values)
    if not values:
        return float("nan")
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def describe(path: Path) -> dict:
    run = json.loads(path.read_text())
    users = run["users"]
    samples = run["samples"]
    by_action = defaultdict(list)
    worst = defaultdict(float)
    for at, action, seconds, user in samples:
        by_action[action].append(seconds)
        if not action.startswith("refresh"):
            worst[user] = max(worst[user], seconds)
    troubled = {user for _, user, _ in run["errors"]}
    kinds = Counter()
    for _, _, text in run["errors"]:
        head = text.split(": ", 2)
        kinds[": ".join(head[:2])[:70]] += 1
    timeline = defaultdict(list)
    for at, action, seconds, _ in samples:
        if action == "answer":
            timeline[int(at // 60)].append(seconds)
    # Students with a paper open in each minute: from Start until they submitted
    # (or stopped), so latency can be read against how many were really working.
    end = max((s[0] for s in samples), default=0)
    stopped = {u: at for at, u, text in run["errors"] if ": " in text and text.split(":")[0] in
               ("answering", "submitting", "waiting for time-up")}
    active = Counter()
    for index, user in enumerate(users):
        if "started" not in user:
            continue
        until = user.get("submitted_at") or stopped.get(index) or end
        for minute in range(int(user["started"] // 60), int(until // 60) + 1):
            active[minute] += 1
    slow_answers = sum(1 for s in by_action.get("answer", []) if s > 3)
    return {
        "file": path.name,
        "users": len(users),
        "outcomes": Counter(u["outcome"] for u in users),
        "students_with_any_error": len(troubled),
        "error_kinds": kinds,
        "actions": {a: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "max": max(v)}
                    for a, v in sorted(by_action.items())},
        "answers_over_3s": slow_answers,
        "worst_wait_per_student": sorted(worst.values()),
        "timeline": {minute: (active.get(minute, 0), len(v), pct(v, .5), pct(v, .95))
                     for minute, v in sorted(timeline.items())},
        "unconfirmed_answers": sum(len(u.get("unconfirmed", [])) for u in users),
        "told_unanswered": sum(1 for u in users if u.get("told_unanswered")),
        "mark_missing": sum(u.get("mark_missing", 0) for u in users),
        "health_max": max((h for _, h in run["health"] if h is not None), default=float("nan")),
        "harness_cpu_peak": max((c for _, c in run["cpu"]), default=0),
    }


def main() -> None:
    for name in sys.argv[1:]:
        d = describe(Path(name))
        print(f"\n=== {d['file']}: {d['users']} students")
        print("outcomes:", dict(d["outcomes"]))
        print(f"students who hit any error: {d['students_with_any_error']}   "
              f"answers not confirmed: {d['unconfirmed_answers']}   told 'unanswered' at submit: {d['told_unanswered']}"
              f"   'Saved' mark lost (header caught up): {d['mark_missing']}")
        for kind, count in d["error_kinds"].most_common():
            print(f"   {count:4d} x {kind}")
        print(f"{'action':22s} {'n':>5s} {'p50':>6s} {'p95':>6s} {'max':>6s}")
        for action, row in d["actions"].items():
            print(f"{action:22s} {row['n']:5d} {row['p50']:6.2f} {row['p95']:6.2f} {row['max']:6.2f}")
        worst = d["worst_wait_per_student"]
        if worst:
            print(f"worst single wait per student: median {pct(worst, .5):.1f}s, "
                  f"worst student {worst[-1]:.1f}s, students who waited >5s at least once: "
                  f"{sum(1 for w in worst if w > 5)}")
        print(f"answers slower than 3s: {d['answers_over_3s']}")
        print("by minute: students working | answers | answer p50 | p95")
        for m, (working, n, a, b) in d["timeline"].items():
            print(f"   m{m:<3d} {working:4d} {n:5d} {a:7.1f}s {b:6.1f}s")
        print(f"health endpoint max {d['health_max']:.2f}s; harness cpu peak {d['harness_cpu_peak']:.0%}")


if __name__ == "__main__":
    main()
