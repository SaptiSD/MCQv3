# Load test — MCQ v3

## Update, 17 Oct 2026: `5227596` on quiz.batchllm-workspace.info

**Answer now: yes for 35, with room to spare.** Hosting moved to
https://quiz.batchllm-workspace.info, and `5227596` makes each question its own
fragment, runs the countdown in the browser with a 15 s server tick, and drops
the dashboard's timer while a paper is open.

**35 students, everything at once** (sign in within 10 s, Start within 5 s,
5–15 s per answer, *all submitted within 56 seconds*):

| Action | median | p95 | worst |
|---|---|---|---|
| Answer saved and shown | 0.64 s | 1.9 s | 3.3 s |
| Submit | 1.65 s | 2.5 s | 3.1 s |
| Sign in | 1.9 s | 2.7 s | 2.9 s |
| Start | 6.3 s | 7.2 s | 7.3 s |

0 errors, 0 unconfirmed answers, every stored answer and score verified against
the clicks. Start is the slowest moment: 35 papers frozen and drawn at once.

**Ceiling, realistic pace** (20–40 s per answer, up to 100 students joining over
15 minutes):

| Students working | Answer median | Answer p95 |
|---|---|---|
| 12–46 | 0.4–0.7 s | 0.6–1.1 s |
| 57 | 1.0 s | 2.3 s |
| 63 | 1.8 s | 6.8 s |
| 70–80 | 7.5–29 s | 18–54 s (cliff; run aborted by the watchdog) |

Comfortable to ~50 at once, usable to ~57, falls over around 65.

**Known residual:** Streamlit can drop a finished run's last messages when a timer
request makes it start a fresh runner first. Here that means roughly one "Saved"
mark in 300–1000 answers not appearing. The answer is stored either way, and the
header's count corrects within 15 s.

Everything below is the original 15 Oct report, against Community Cloud.

---

## Original report: swirl-mcq.streamlit.app, 15 Oct 2026

**Question:** can ~30–35 students take one assessment at the same time, with nobody
seeing lag or errors? And where does it break?

**Answer:** not yet, on Streamlit Community Cloud.

| Build | Safe (p95 answer ≤ 4 s, zero errors) | Breaks at |
|---|---|---|
| Before `e3f7fe4` | **No safe size**: errors from 5 students | 35: 7-minute stall, half the class dropped, answers silently lost |
| `e3f7fe4` (deployed now) | **~20 concurrent students** | **~23–25**: a cliff, where *everyone* stalls, not just the extra students |

35 students on the current deployment would stall the whole class.

## Method

- **Target:** the deployed app (`https://swirl-mcq.streamlit.app/~/+`, Streamlit 1.66 /
  Python 3.14 on Community Cloud) and the live Supabase database. All test data was
  `ZZTEST`-prefixed and has been deleted (151 users and every test quiz and attempt).
- **Students:** `loadtest/swarm.py` simulates each student as a real Streamlit session.
  It speaks the browser's websocket/protobuf protocol, including the timer reruns of
  `run_every` fragments. It doesn't run Chromium, so 100 students fit on one laptop;
  browser rendering time is excluded, while everything the server does is measured.
- **Each student:** opens the page, signs in on the Students panel, presses Start, and
  answers all 30 questions in their own shuffled order. Then they submit, or wait for
  time-up. Latency is measured from the click to the end of the run that shows its
  effect (e.g. "N of 30 answered" ticking up).
- **Paper:** 30 questions (25 MCQ, 3 T/F, 1 select-all, 1 fill-in-the-blank) with
  an ~8 KB balance-sheet Description/Context, the same shape as the real Quiz 4.
- **Integrity:** `seed.py verify` checks every stored answer and score against what
  each student actually clicked.
- **Pace matters:** a real student on a 30-question / 30-minute paper answers about
  once a minute. Runs at 5–15 s per answer are **stress tests** (3–5× a real class),
  and the 20–40 s ramp is the realistic one.

## Results

### Single student (the floor)

| | Before | After `e3f7fe4` |
|---|---|---|
| Answer saved and shown | 1.04 s | **0.61 s** |
| Clock / paper refresh | 1.19 s (whole paper) | **0.20 s** (clock only) |
| Sign in / Start / Submit | 1.4 / 2.1 / 1.8 s | 1.8 / 2.5 / 1.6 s |

### Old build, 35 students at stress pace (5–15 s per answer)

- From minute 3, almost no answers saved for **7 minutes**. Refreshes took up to 810 s.
- **17 students disconnected** within the same 10 seconds; **15 of 35 submitted**.
- 272 answers were never confirmed. Of the 15 who submitted, 11 got "N questions are
  still unanswered" at Submit. **17 answers across 11 students were shown as selected
  but never stored**, and 9 of those students were marked 3–10 points low.
- Errors on screen: `httpx.WriteError`, `ReadError`, `RemoteProtocolError`, and a false
  "You had this assessment open in more than one place".
- With **5 students**, one already hit `RemoteProtocolError` and was stuck.

### Fixed build, realistic ramp (20–40 s per answer, students joining over 15 min)

| Students working | Answers | Answer p50 | Answer p95 |
|---|---|---|---|
| 6 | 4 | 1.1 s | 1.2 s |
| 10 | 12 | 0.9 s | 1.3 s |
| 13 | 19 | 1.0 s | 1.2 s |
| 17 | 29 | 1.3 s | 3.9 s |
| 20 | 30 | 1.4 s | 2.1 s |
| **25** | 37 → 22 | **4.1 s → 47 s** | **9.4 s → 58 s** |

The health watchdog stopped the run at 27 students. Zero errors below 20.

### Fixed build, stress pace at 35

- **5–15 s per answer:** stalled from minute 3. The run was stopped after 20 minutes.
- **3–7 s per answer, time-up after 8 minutes:** answers took 16–44 s and Start took
  15 s (p50). **Every one of the 34 papers was still handed in by time-up server-side**,
  with `auto_submitted` set, even while the server was overloaded.

### Not measured

The "everyone presses Submit within one minute" run on the fixed build. The laptop
driving it slipped into Modern Standby (Windows event log, 12:39:46–12:54:51), which
suspended the harness, and the run produced nothing. The time-up run above is the
harsher form of the same thing: 34 submissions within one refresh interval.

## Why it breaks

1. **A connection bug (fixed).** Every student session shares one Supabase client, and
   its HTTP/2 transport lets those threads drive a single connection at the same time.
   httpcore 1.0.9's `send_headers` takes no lock, so two students clicking together
   corrupt the connection's shared header-compression state. Supabase's gateway then
   drops it (`PROTOCOL_ERROR`, a bare HTML 400, `KeyError: <stream id>`), and every
   request in flight on it fails at once. That is what lost answers silently: the radio
   keeps showing the choice, but the write never landed.
2. **Too much work per student (reduced).** The open paper re-ran every 5 seconds just
   to move the clock: every question, the 8 KB context and three queries, two of them
   identical for every student in the room.
3. **The container (remaining).** Profiling the server under load showed it pinned at
   one core. Community Cloud gives the app a small slice of a shared CPU, so per-click
   cost now decides the ceiling. Supabase is not the limit: the same client handles
   ~350 requests/s from 35 threads outside Streamlit.

## Bugs

```
MCQ-BUG-032 — Simultaneous answers corrupt the shared database connection and lose answers
Bug Description   All sessions share one HTTP/2 connection to Supabase. When several
                  students save at the same moment, the connection's state is
                  corrupted, Supabase drops it, and every in-flight request fails. The
                  student sees a traceback, or nothing at all: the radio stays
                  selected but the answer is not stored.
Steps to Reproduce  1. Seed: python loadtest/seed.py accounts --students 35 --out a.json
                    2. python loadtest/seed.py quiz --accounts a.json --title "ZZTEST x" --out q.json
                    3. On a build before e3f7fe4: python loadtest/swarm.py --base <app>/~/+
                       --accounts a.json --quiz q.json --users 10 --think-min 1 --think-max 2 --out r.json
                    4. python loadtest/seed.py verify --quiz q.json --results r.json
Expected Behavior   Every answer a student picks is stored; no tracebacks.
Observed Behavior   httpx ReadError / WriteError / RemoteProtocolError on screen from 5
                    students; at 35, 17 answers across 11 students shown as picked but
                    never stored, scores 3–10 points low, 17 students disconnected.
Impact              Every student in a class sitting a test together. Wrong marks
                    with no visible sign.
Status              Fixed in e3f7fe4 (HTTP/1.1 pool).
```

```
MCQ-BUG-033 — A class of more than ~22 sitting one paper stalls everybody
Bug Description   Per-student server work is high enough that Community Cloud's CPU
                  saturates at ~23–25 students answering at a normal pace. Past that
                  point, requests queue without bound: answers take 45 s+, the clock
                  freezes and new students can't load the page.
Steps to Reproduce  1. Seed as in MCQ-BUG-032 with --students 60.
                    2. python loadtest/swarm.py ... --users 60 --arrive-spread 900
                       --start-at 0 --login-spread 0 --start-spread 0 --think-min 20
                       --think-max 40 --abort-after 60 --out r.json
                    3. python loadtest/report.py r.json  (see "by minute")
Expected Behavior   35 students answer with ≤ 2–3 s per click.
Observed Behavior   Fine to 20; at 25 the median answer goes 4 s → 47 s within a minute.
Impact              Any class above ~20 on the current hosting.
Status              Reduced in e3f7fe4 (fewer queries, only the clock on a timer);
                    not solved. See "To reach 35" below.
```

## What changed (`e3f7fe4`, pushed to `main`)

- `db.py`: HTTP/1.1 instead of HTTP/2, with a 40-connection pool kept warm.
- `repository.py`: the quiz row and paper fingerprint an open attempt reads are
  shared across sessions for 5 s. Both lock once a student starts, and marking, the
  paper lock and the editors still read live. `available_quizzes` asks for the
  student's own quizzes, not every active quiz in the database.
  `attempts_for_student` no longer pulls every paper's answers. New
  `attempt_is_open` check.
- `student_portal.py`: only `_clock` runs on a 5 s timer, and it checks the database
  every 30 s. The paper redraws when the student acts. Time-up still submits on the
  tick it happens: within 3 s locally, and all 34 papers in the 35-student cloud run.
  The dashboard list holds its last reading while a paper is open.

The commit's title says "Keep a class of 35 working at once". That holds for the
errors and lost answers; for capacity on Community Cloud it overstates. The ceiling
is ~22.

## To reach 35 safely (with buffer)

In order of certainty:

1. **For the upcoming test:** two sittings of ≤ 17, or a shorter paper / smaller
   context block. Both stay inside the measured safe zone.
2. **Hosting with dedicated CPU** (a small VM or a paid container with 1–2 vCPU). The
   container's CPU slice is now the limit, and Community Cloud has no paid tier to
   raise it. Re-run the realistic ramp there before trusting it.
3. **Cut per-click work further:** make each question its own fragment, so an answer
   redraws one question instead of 30 plus the context. Also tick the clock every 10–15 s,
   and skip the second read of the attempt right after saving it. Unmeasured; needs
   the ramp again.

## Rerunning

```bash
python loadtest/seed.py accounts --students 60 --out <scratch>/accounts.json
python loadtest/seed.py quiz --accounts <scratch>/accounts.json --title "ZZTEST ramp" --students 60 --out <scratch>/quiz.json
python loadtest/swarm.py --base https://quiz.batchllm-workspace.info --accounts <scratch>/accounts.json --quiz <scratch>/quiz.json --users 60 --arrive-spread 900 --login-spread 0 --start-at 0 --start-spread 0 --think-min 20 --think-max 40 --abort-after 60 --max-seconds 1500 --out <scratch>/ramp.json
python loadtest/report.py <scratch>/ramp.json
python loadtest/seed.py cleanup
```

Always pass `--abort-after` against the live app, and keep the driving laptop awake
(Modern Standby suspends the harness).
