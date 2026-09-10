"""Checks for the Supabase transport's retry policy (db.py).

A Streamlit app leaves its connection pool idle between clicks, so it regularly
hands a request to a socket the gateway has already closed. These cover what the
transport does about that, and — just as importantly — what it refuses to do
about it when repeating the request could write the same row twice.

Run with: python test_db_retry.py
"""

import httpx

import db

passed = failed = 0


def check(label, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  pass {label}")
    else:
        failed += 1
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")


class _Recorder(db._ResilientTransport):
    """Stands in for the real network: fails a set number of times, then answers."""

    def __init__(self, failures, error):
        super().__init__()
        self.remaining = failures
        self.error = error
        self.calls = 0

    def handle_request(self, request):
        # Deliberately skips HTTPTransport's own send and re-enters the retry
        # loop in the parent, so only the retry policy is under test.
        return db._ResilientTransport.handle_request(self, request)

    def _send(self, request):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.error
        return httpx.Response(200, content=b"[]")


def attempt(method, failures, error):
    """Drive one request through the retry loop and report calls plus outcome."""
    recorder = _Recorder(failures, error)
    # Patch the one-shot send the transport delegates to.
    original = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: self._send(request)
    try:
        request = httpx.Request(method, "https://example.test/rest/v1/quizzes")
        try:
            response = recorder.handle_request(request)
        except Exception as exc:
            return recorder.calls, type(exc).__name__
        return recorder.calls, response.status_code
    finally:
        httpx.HTTPTransport.handle_request = original


read_error = httpx.ReadError("[WinError 10035] would block")
connect_error = httpx.ConnectError("connection refused")

print("== a read that dies on a stale connection ==")
check("GET recovers on the second try", attempt("GET", 1, read_error), (2, 200))
check("GET keeps trying up to three times", attempt("GET", 2, read_error), (3, 200))
check("GET gives up after three", attempt("GET", 3, read_error), (3, "ReadError"))

print("\n== a write that dies mid-request is NOT repeated ==")
# The server may already have applied it; a retry would insert the row twice.
check("POST surfaces the read error at once", attempt("POST", 1, read_error), (1, "ReadError"))
check("PATCH surfaces the read error at once", attempt("PATCH", 1, read_error), (1, "ReadError"))

print("\n== a write that never reached the server IS repeated ==")
check("POST retries a refused connection", attempt("POST", 1, connect_error), (2, 200))
check("GET retries a refused connection", attempt("GET", 1, connect_error), (2, 200))

print("\n== a healthy request is sent once ==")
check("no retry when nothing fails", attempt("GET", 0, read_error), (1, 200))
check("POST sent once", attempt("POST", 0, read_error), (1, 200))

print(f"\n{passed} passed, {failed} failed.")
if failed:
    raise SystemExit(1)
print("All transport-retry tests passed.")
