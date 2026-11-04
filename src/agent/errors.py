"""
A failed model call in one sentence that names the provider and says what to do (AutoMC_Agent_Models_Plan.md, MS-4): a missing or
wrong key, refused access, an unknown model, a rate limit or spent quota, a request too long for the model, a malformed request, a
server error, a timeout, a service that does not answer. Never the key.

The services raise different exceptions (openai's APIStatusError with status_code; google-genai's ClientError with code and status,
wrapped by langchain-google-genai; httpx's ConnectError for Ollama), so the classification reads the HTTP status and the class names
along the exception's chain of causes, and the text as a last resort. No service package is imported here.
"""
from __future__ import annotations

import re

_STATUS_TEXT = re.compile(r"(?:Error code|HTTP|status(?: code)?)\D{0,3}([45]\d\d)\b|\b([45]\d\d) [A-Z][A-Z_]{3,}\b")


def _chain(e):
    seen = []
    while e is not None and all(e is not s for s in seen):
        seen.append(e)
        e = e.__cause__ or e.__context__
    return seen


def _status(chain, text):
    for e in chain:
        for attr in ("status_code", "code"):
            v = getattr(e, attr, None)
            if isinstance(v, int) and not isinstance(v, bool) and 400 <= v < 600:
                return v
        v = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(v, int) and 400 <= v < 600:
            return v
    m = _STATUS_TEXT.search(text)
    return int(m.group(1) or m.group(2)) if m else None


def kind(e):
    """key, denied, model, rate, too_long, refused, server, timeout, unreachable or other."""
    chain = _chain(e)
    names = " ".join(type(x).__name__ for x in chain)
    text = " ".join(str(x) for x in chain)
    low = text.lower()
    status = _status(chain, text)
    if "ContextOverflow" in names or "context_length_exceeded" in low or "maximum context length" in low or \
            "exceeds the maximum number of tokens" in low or "token limit" in low:
        return "too_long"
    if status == 401 or "Authentication" in names or "api key not valid" in low or "incorrect api key" in low or \
            "invalid api key" in low or "unauthenticated" in low:
        return "key"
    if status == 429 or "RateLimit" in names or "resource_exhausted" in low or "rate limit" in low or "quota" in low:
        return "rate"
    if status == 403 or "PermissionDenied" in names or "permission_denied" in low:
        return "denied"
    if status == 404 or "NotFound" in names or "model_not_found" in low or ("model" in low and "not found" in low):
        return "model"
    if "Timeout" in names or "timed out" in low or "deadline_exceeded" in low:
        return "timeout"
    if status is not None and status >= 500 or "ServerError" in names or "InternalServer" in names or "unavailable" in low:
        return "server"
    if status in (400, 422) or "BadRequest" in names or "invalid_argument" in low:
        return "refused"
    if "Connect" in names or "ConnectionError" in names or "connection refused" in low or "name or service not known" in low:
        return "unreachable"
    return "other"


def _detail(e, p, n=180):
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    key = p.key() if hasattr(p, "key") else ""
    if key:
        text = text.replace(key, "***")
    from agent.llm import screen_name
    text = screen_name(text)                          # a service's message may quote the model id with a Portkey @slug/
    return text if len(text) <= n else text[:n - 3] + "..."


def explain(e, p):
    """(kind, one sentence) for exception `e` raised by a call to provider `p`."""
    k = kind(e)
    from agent.llm import bare_model
    name, model = p.name, bare_model(p.model or "") or "the model"
    detail = _detail(e, p)
    text = {
        "key": f"{name} refused the key in {p.api_key_env} (missing, wrong or expired); check it where it is set (the shell, or the keys file AUTOMC_ENV_FILE names).",
        "denied": f"{name} refused access to {model} (HTTP 403): the key may not cover this model, or the account has no billing.",
        "model": f"{name} does not know the model {model}; /model lists the models it offers.",
        "rate": f"{name} is limiting requests or the account is out of quota (HTTP 429); wait a minute, or switch with /model.",
        "too_long": f"The request is too long for {model}: the conversation and AutoMC's tools exceed its context; /new starts a "
                    f"short conversation, /model picks a model with a larger window.",
        "refused": f"{name} refused the request as malformed ({detail}); if it names a tool or a schema, `python -m automc models "
                   f"check {getattr(p, 'label', p.name)} --tools` shows whether AutoMC's tool definitions pass.",
        "server": f"{name} had a server error ({detail}).",
        "timeout": f"{name} did not answer within {p.timeout_s:.0f} s" + (" (a local model may need longer: timeout_s in llm.yaml)."
                                                                         if not p.cloud else "."),
        "unreachable": (f"nothing answers at {p.base_url}: is Ollama running in this environment (`ollama serve`)?" if not p.cloud
                        else f"{name} at {p.base_url} is not reachable (no network, or a proxy in the way)."),
        "other": f"{name}: {type(e).__name__}: {detail}",
    }[k]
    return k, text
