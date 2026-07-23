#!/usr/bin/env python3
"""
resilient.py — Shared retry / back-off for all report scripts.

Handles transient failures from Azure Cost Management, Databricks REST,
Microsoft Graph, REDCap, ADF, and any other HTTP API used in this project.

Quick start
-----------
    from resilient import resilient_post, resilient_get, AZURE_POLICY

    resp = resilient_post(url, policy=AZURE_POLICY, label="cost-query",
                          headers=headers, json=payload)
    data = resp.json()

Pre-built policies
------------------
    AZURE_POLICY        — Cost Management, ARM, ADF, Key Vault, etc.
    DATABRICKS_POLICY   — Databricks REST API
    GRAPH_POLICY        — Microsoft Graph (Teams, SharePoint, Entra)
    REDCAP_POLICY       — REDCap API
    DEFAULT_POLICY      — Generic 429/503 + connection errors
"""

import sys
import time
import requests


# ── Retry policy ───────────────────────────────────────────────────────────────

class RetryPolicy:
    """
    Encapsulates when and how to retry a failed HTTP call.

    Parameters
    ----------
    max_retries : int
        Total attempts including the first (default 4 = 1 try + 3 retries).
    backoff_base : float
        Seconds added per attempt: attempt 1 waits backoff_base, attempt 2
        waits 2×backoff_base, etc.  (linear, not exponential, so delays are
        predictable and logs are readable.)
    retry_status : tuple[int, ...]
        HTTP status codes that warrant a retry (e.g. 429, 503).
    retry_exc : tuple[type, ...]
        Exception types that warrant a retry (connection errors, timeouts).
    retry_if : callable | None
        Optional ``fn(response) -> bool`` for APIs that signal transient
        errors inside a 200-OK body (e.g. Azure Cost Management RBAC flap).
    timeout : float
        Per-request connect+read timeout in seconds passed to requests.
    """

    def __init__(
        self,
        max_retries: int = 4,
        backoff_base: float = 5.0,
        retry_status: tuple = (429, 502, 503),
        retry_exc: tuple = (requests.Timeout, requests.ConnectionError),
        retry_if=None,
        timeout: float = 90.0,
    ):
        self.max_retries  = max_retries
        self.backoff_base = backoff_base
        self.retry_status = frozenset(retry_status)
        self.retry_exc    = retry_exc
        self.retry_if     = retry_if
        self.timeout      = timeout

    def wait(self, attempt: int) -> float:
        """Linear back-off: backoff_base × (attempt + 1)."""
        return self.backoff_base * (attempt + 1)

    def is_retryable_exc(self, exc: Exception) -> bool:
        return isinstance(exc, self.retry_exc)

    def is_retryable_response(self, resp: requests.Response) -> bool:
        if resp.status_code in self.retry_status:
            return True
        if self.retry_if is not None:
            try:
                return bool(self.retry_if(resp))
            except Exception:
                return False
        return False


# ── Per-API transient-error predicates ────────────────────────────────────────

def _azure_body_retry(resp: requests.Response) -> bool:
    """
    Azure Cost Management (and occasionally other ARM APIs) returns HTTP 200
    with an error body for transient infrastructure issues such as the
    RBACAccessDenied / NoHttpContext flap seen under concurrent load.
    """
    try:
        err = resp.json().get("error", {})
        code = err.get("code", "")
        msg  = err.get("message", "")
        return (
            (code == "RBACAccessDenied" and "NoHttpContext" in msg)
            or code in ("TooManyRequests", "BillingAccountNotFound")
        )
    except Exception:
        return False


def _graph_body_retry(resp: requests.Response) -> bool:
    """
    Microsoft Graph sometimes returns 200 with a throttle payload, or
    returns a Retry-After header on 429s.  The status-code check covers
    the 429 case; this handles body-level signals.
    """
    try:
        err = resp.json().get("error", {})
        return err.get("code", "") in ("activityLimitReached", "serviceNotAvailable")
    except Exception:
        return False


def _databricks_body_retry(resp: requests.Response) -> bool:
    """Databricks REST can return 200 with RESOURCE_EXHAUSTED or TEMPORARILY_UNAVAILABLE."""
    try:
        body = resp.json()
        ec = body.get("error_code", "")
        return ec in ("RESOURCE_EXHAUSTED", "TEMPORARILY_UNAVAILABLE", "REQUEST_LIMIT_EXCEEDED")
    except Exception:
        return False


# ── Pre-built policies ─────────────────────────────────────────────────────────

AZURE_POLICY = RetryPolicy(
    max_retries=5,
    backoff_base=5,
    retry_status=(429,),          # ARM/Cost Mgmt HTTP-level throttle
    retry_if=_azure_body_retry,   # body-level transient errors (200 OK)
    timeout=90,
)

DATABRICKS_POLICY = RetryPolicy(
    max_retries=4,
    backoff_base=5,
    retry_status=(429, 503, 502),
    retry_if=_databricks_body_retry,
    timeout=60,
)

GRAPH_POLICY = RetryPolicy(
    max_retries=4,
    backoff_base=6,
    retry_status=(429, 503),
    retry_if=_graph_body_retry,
    timeout=30,
)

REDCAP_POLICY = RetryPolicy(
    max_retries=3,
    backoff_base=4,
    retry_status=(500, 502, 503),
    timeout=60,
)

DEFAULT_POLICY = RetryPolicy()


# ── Core request wrapper ───────────────────────────────────────────────────────

def _resilient_request(
    method: str,
    url: str,
    policy: RetryPolicy,
    label: str,
    **kwargs,
) -> requests.Response:
    """
    Execute an HTTP request with retry / back-off per policy.

    Returns ``requests.Response`` on success.
    Raises ``RuntimeError`` after all retries are exhausted.
    Re-raises non-retryable exceptions immediately so callers can
    distinguish "API down" from "gave up on transient error".
    """
    kwargs.setdefault("timeout", policy.timeout)
    last_exc = None

    for attempt in range(policy.max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
        except Exception as exc:
            last_exc = exc
            if policy.is_retryable_exc(exc):
                wait = policy.wait(attempt)
                print(
                    f"  [{label}] connection error"
                    f" (attempt {attempt + 1}/{policy.max_retries}):"
                    f" {str(exc)[:120]} — retrying in {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise  # non-retryable: propagate immediately

        if policy.is_retryable_response(resp):
            wait = policy.wait(attempt)
            try:
                code = (
                    resp.json()
                        .get("error", resp.json())
                        .get("code", resp.status_code)
                )
            except Exception:
                code = resp.status_code
            print(
                f"  [{label}] transient {code}"
                f" (attempt {attempt + 1}/{policy.max_retries})"
                f" — retrying in {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
            continue

        return resp  # success

    msg = f"[{label}] gave up after {policy.max_retries} attempts"
    if last_exc:
        raise RuntimeError(msg) from last_exc
    raise RuntimeError(msg)


# ── Public HTTP helpers ────────────────────────────────────────────────────────

def resilient_get(
    url: str,
    *,
    policy: RetryPolicy | None = None,
    label: str = "GET",
    **kwargs,
) -> requests.Response:
    """GET with retry.  All extra kwargs are forwarded to requests."""
    return _resilient_request("GET", url, policy or DEFAULT_POLICY, label, **kwargs)


def resilient_post(
    url: str,
    *,
    policy: RetryPolicy | None = None,
    label: str = "POST",
    **kwargs,
) -> requests.Response:
    """POST with retry.  All extra kwargs are forwarded to requests."""
    return _resilient_request("POST", url, policy or DEFAULT_POLICY, label, **kwargs)


# ── Generic callable wrapper ───────────────────────────────────────────────────

def retry_call(fn, *args, policy: RetryPolicy | None = None, label: str = "call", **kwargs):
    """
    Call any function with retry on retryable exceptions.

    Unlike the HTTP helpers, this only retries on exceptions — there is no
    HTTP response to inspect.  Useful for SDK calls, subprocess wrappers,
    database queries, or any other I/O operation.

    Returns the function's return value on success.
    Raises ``RuntimeError`` if all retries are exhausted.
    Re-raises non-retryable exceptions immediately.

    Example
    -------
        result = retry_call(
            my_sdk_client.list_items,
            policy=DATABRICKS_POLICY,
            label="list-notebooks",
        )
    """
    p = policy or DEFAULT_POLICY
    last_exc = None

    for attempt in range(p.max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if p.is_retryable_exc(exc):
                wait = p.wait(attempt)
                print(
                    f"  [{label}] error"
                    f" (attempt {attempt + 1}/{p.max_retries}):"
                    f" {str(exc)[:120]} — retrying in {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                raise  # non-retryable

    raise RuntimeError(
        f"[{label}] gave up after {p.max_retries} attempts"
    ) from last_exc
