"""
Which models the agent may be given (AutoMC_Agent_Models_Plan.md, MS-3): the model each provider in config/llm.yaml names, and the
models a provider with `discover:` reports, filtered so that only models able to drive the agent reach the chat:

  ollama   local models whose Ollama capabilities include tools, vision and thinking, each at the context it is served at (num_ctx
           in its parameters, else the provider's num_ctx; model_info can show twice the real window, a finding recorded in RADIANT-LLM)
  openai   OpenAI's GPT models from gpt-4 on, directly or through Portkey (ids written @<slug>/<model>)
  gemini   Gemini models from gemini-3 on that support generateContent

The cloud filters are RADIANT-LLM's (utils/cloud_model_catalog.py, where each exclusion's reason is recorded), with one addition: only
gpt- and gemini- models, so the other providers of a Portkey workspace and Google's Gemma models stay out. No other service is
discovered. A discovered model takes its kind and account from its provider: discovery never decides who may see what.

A cloud model can be listed and still refuse every call (RADIANT-LLM found this for Gemini), so a discovered cloud model is used only
after `python -m automc models check PROVIDER/MODEL --tools` has passed once; the result is kept in the workspace
(.automc/models/checks.json: whether it passed and when, never a key or a message). Cloud lists are cached for an hour (refresh=True
lists again; a failed listing serves the last one); the local list is read each time.

Only the standard library: the lists come from plain HTTP requests, so the picker works without any model package installed.
"""
from __future__ import annotations

import dataclasses
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import automc_paths
from agent import llm

LOCAL_NEEDS = ("tools", "vision", "thinking")
TTL_S = 3600
TIMEOUT_S = 15
OFFERED = {"ollama": "only local models with the tools, vision and thinking capabilities are offered",
           "openai": "only GPT models from gpt-4 on are offered (RADIANT-LLM's filters)",
           "gemini": "only Gemini models from gemini-3 on are offered (RADIANT-LLM's filters)"}

# RADIANT-LLM's filters (utils/cloud_model_catalog.py, 2026-10-22), as they are there.
NON_CHAT = ("embedding", "whisper", "tts", "audio", "image", "vision-preview", "transcribe", "moderation", "dall-e", "davinci",
            "babbage", "ada", "curie", "search", "similarity", "edit", "realtime", "computer-use", "instruct", "sora", "lyria",
            "robotics", "live")
DATED = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{3,4})$")       # pinned snapshots of a listed alias
OPENAI_EXCLUDED = ("gpt-6-astra", "chatgpt-4o-latest", "gpt-4o-latest", "gpt-4-0125-preview", "gpt-4-1106-preview",
                   "gpt-4-turbo-preview", "gpt-4-32k", "chat-latest")
GEMINI_EXCLUDED = ("antigravity-preview-latest",)
GPT = re.compile(r"^gpt-(\d+)")                            # gpt- only: also leaves out the o-series, as RADIANT-LLM does
GPT_VERSION = re.compile(r"^gpt-(\d+(?:\.\d+)?)")
GEMINI = re.compile(r"^gemini-(\d+)")
LABEL = re.compile(r"^(local|cloud)-([^/]+)/(.+)$")             # a model as the screen shows it (llm.Provider.label)

_CACHE = {}


class NotChecked(llm.LLMConfigError):
    """A discovered cloud model that has not passed `models check --tools` yet."""


# ---- the filters (pure: a provider's raw list in, the models offered and those left out) ----------------------------------------
def _chat(model_id):
    low = model_id.lower()
    return not any(f in low for f in NON_CHAT) and not DATED.search(model_id)


def _bare(model_id):
    """gpt-5.4 for @slug/gpt-5.4 (Portkey's ids), else the id."""
    return model_id.split("/", 1)[1] if model_id.startswith("@") and "/" in model_id else model_id


def _version(model_id):
    m = GPT_VERSION.match(model_id)
    return (float(m.group(1)) if m else -1.0, model_id)


def openai_models(data):
    """OpenAI's /models entries ({id, created}) -> (offered [{model}], left out [ids]), newest first: by created when the service
    gives it (OpenAI does), else by the version in the name (Portkey gives none)."""
    if any(m.get("created") for m in data):
        order = sorted(data, key=lambda m: m.get("created") or 0, reverse=True)
    else:
        order = sorted(data, key=lambda m: _version(_bare(m.get("id", ""))), reverse=True)
    offered, left = [], []
    for m in order:
        mid = m.get("id", "")
        b = _bare(mid)
        v = GPT.match(b)
        if (v and int(v.group(1)) >= 4 and _chat(b) and "codex" not in b and b not in OPENAI_EXCLUDED and not b.endswith("-pro")
                and not b.endswith("-chat-latest") and not b.startswith("gpt-live")):
            offered.append({"model": mid})
        else:
            left.append(mid)
    return offered, left


def gemini_models(raw):
    """Gemini's /models entries ({name, supportedGenerationMethods, inputTokenLimit}) -> (offered [{model, context_window}], left out),
    newest first (the list runs oldest to newest; reversed, as RADIANT-LLM does)."""
    offered, left = [], []
    for m in reversed(raw):
        short = (m.get("name") or "").split("/", 1)[-1]
        v = GEMINI.match(short)
        if ("generateContent" in (m.get("supportedGenerationMethods") or []) and short.startswith("gemini-")
                and not (v and int(v.group(1)) < 3) and "omni" not in short and _chat(short) and short not in GEMINI_EXCLUDED):
            offered.append({"model": short, "context_window": m.get("inputTokenLimit")})
        else:
            left.append(short)
    return offered, left


def ollama_models(tags, show):
    """Ollama's /api/tags and a function name -> /api/show -> (offered [{model, num_ctx}], left out ["name (lacks ...)"])."""
    offered, left = [], []
    for t in tags.get("models") or []:
        name = t.get("name") or t.get("model")
        s = show(name)
        lacks = [c for c in LOCAL_NEEDS if c not in (s.get("capabilities") or [])]
        if lacks:
            left.append(f"{name} (lacks {', '.join(lacks)})")
            continue
        n = re.search(r"^num_ctx\s+(\d+)", s.get("parameters") or "", re.MULTILINE)
        offered.append({"model": name, "num_ctx": int(n.group(1)) if n else None})
    return offered, left


# ---- asking the services ------------------------------------------------------------------------------------------------------
def _request(url, headers=None, body=None, timeout=TIMEOUT_S):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _headers(p):
    return {"User-Agent": "AutoMC", **p.headers}                # Portkey's front refuses requests without a named User-Agent


def _error(e, p):
    """One line for a failed listing; never the key."""
    if isinstance(e, urllib.error.HTTPError):
        text = f"HTTP {e.code} {e.reason}"
    elif isinstance(e, urllib.error.URLError):
        text = f"not reachable ({e.reason})"
    else:
        text = f"{type(e).__name__}: {e}"
    key = p.key()
    return (text.replace(key, "***") if key else text)[:200]


def _fetch(p):
    base = p.base_url.rstrip("/")
    if p.discover == "ollama":
        return ollama_models(_request(base + "/api/tags", _headers(p)),
                             lambda name: _request(base + "/api/show", _headers(p), {"model": name}))
    if p.discover == "openai":
        return openai_models(_request(base + "/models", {**_headers(p), "Authorization": f"Bearer {p.key()}"}).get("data") or [])
    base = re.sub(r"/openai$", "", base)                         # Gemini's own API lists the models; its OpenAI endpoint does not say
    raw, token = [], None                                        # which support generateContent
    for _ in range(20):
        q = urllib.parse.urlencode({"pageSize": 1000, **({"pageToken": token} if token else {})})
        d = _request(f"{base}/models?{q}", {**_headers(p), "x-goog-api-key": p.key()})
        raw += d.get("models") or []
        token = d.get("nextPageToken")
        if not token:
            break
    return gemini_models(raw)


def listing(p, refresh=False):
    """What provider `p` offers: {models: [{model, num_ctx | context_window}], left_out: [...], error: None | text}. Never raises."""
    if not p.discover:
        return {"models": [], "left_out": [], "error": None}
    key = (p.name, p.base_url, p.discover)
    hit = _CACHE.get(key)
    if p.cloud:
        if hit and not refresh and time.time() - hit["t"] < TTL_S:
            return hit
        if not p.key():
            return {"models": [], "left_out": [], "error": f"no key in {p.api_key_env}, so its models cannot be listed"}
    try:
        offered, left = _fetch(p)
    except Exception as e:                                       # unreachable, a refused key, a changed format: say so, never raise
        if hit:
            return {**hit, "error": f"listing failed ({_error(e, p)}); showing the list of "
                                    f"{time.strftime('%H:%M', time.localtime(hit['t']))}"}
        return {"models": [], "left_out": [], "error": f"listing failed: {_error(e, p)}"}
    out = {"models": offered, "left_out": left, "error": None, "t": time.time()}
    _CACHE[key] = out
    return out


# ---- the models a user may choose ---------------------------------------------------------------------------------------------
def _key(p, model):
    """Ollama treats name and name:latest as one model."""
    return model + ":latest" if p.backend == "ollama" and model and ":" not in model.rsplit("/", 1)[-1] else model


def _discovered(p, m):
    q = dataclasses.replace(p, model=m["model"], discovered=True)
    if m.get("num_ctx"):
        q = dataclasses.replace(q, num_ctx=m["num_ctx"])         # the context the model is served at, not the provider's
    if m.get("context_window"):
        q = dataclasses.replace(q, context_window=m["context_window"])
    return q


def _row(q):
    miss = q.missing()
    checked = passed(q.choice) if q.cloud and q.discovered else None
    if miss:
        state = "needs " + "; ".join(miss)
    elif checked is False:
        state = "not checked yet (choosing it runs a short check first)"
    else:
        state = "ready"
    return {"choice": q.choice, "label": q.label, "provider": q.name, "model": q.model, "kind": q.kind, "account": q.account,
            "window": q.window,
            "source": "discovered" if q.discovered else "configured", "ready": not miss and checked is not False, "checked": checked,
            "state": state}


def catalog(config=None, refresh=False):
    """
    The models the user may choose, in the order the picker shows them (/model in the chat, the dropdown of the GUI): for each provider
    in llm.yaml its configured model, then the models it discovers, without those a configured provider already offers.
      entries      [{n, choice, provider, model, kind, account, window, source, ready, checked, state}]; `choice` is what
                   /model, --provider and resolve() take
      unavailable  [{provider, reason}]: a provider with nothing to choose, or whose list could not be read
      left_out     [{provider, kind, models}]: what a provider listed and the filters left out
    """
    config = config or llm.load_config()
    provs = llm.providers(config)
    taken = {(p.base_url, _key(p, p.model)) for p in provs if p.model}
    entries, unavailable, left_out = [], [], []
    for p in provs:
        if p.model:
            entries.append(_row(p))
        elif not p.discover:
            unavailable.append({"provider": p.name, "reason": "needs " + "; ".join(p.missing())})
        found = listing(p, refresh)
        if found["error"]:
            unavailable.append({"provider": p.name, "reason": found["error"]})
        if found["left_out"]:
            left_out.append({"provider": p.name, "kind": p.kind, "models": found["left_out"]})
        entries += [_row(_discovered(p, m)) for m in found["models"] if (p.base_url, _key(p, m["model"])) not in taken]
    for i, e in enumerate(entries, 1):
        e["n"] = i
    return {"entries": entries, "unavailable": unavailable, "left_out": left_out}


def _same(p, a, b):
    """True when two model names are one model of provider `p` (with or without a Portkey @slug/, with or without :latest)."""
    return _key(p, llm.bare_model(a)) == _key(p, llm.bare_model(b))


def _from_label(name, config):
    """(provider name, model or None for its configured model) for a choice typed as the screen shows it, KIND-SERVICE/MODEL
    (cloud-portkey/gpt-5.4); None when `name` is not one."""
    m = LABEL.match(name)
    if not m or name.split("/", 1)[0] in config["providers"]:
        return None
    kind, service, model = m.groups()
    provs = [p for p in llm.providers(config) if p.kind == kind and p.service == service]
    if not provs:
        raise llm.LLMConfigError(f"no {kind} provider in llm.yaml is served by {service}; /model lists the models you can choose")
    for p in provs:
        if p.model and _same(p, model, p.model):
            return p.name, None
    finders = [p for p in provs if p.discover]
    if not finders:
        raise llm.LLMConfigError(f"no {kind} provider served by {service} offers {model}; /model lists the models you can choose")
    return finders[0].name, model


def resolve(name=None, require_ready=True, require_checked=True, config=None):
    """
    The provider for a choice: a provider's name (its configured model), PROVIDER/MODEL (a model it discovers; refused unless the
    filters offer it, and for a cloud model, unless it has passed `models check --tools`: NotChecked), or the label the screen shows,
    KIND-SERVICE/MODEL. A Portkey model may be given without its @slug/ when one of the workspace's providers offers it.
    Default: llm.default_name().
    """
    config = config or llm.load_config()
    name = name or llm.default_name(config)
    named = _from_label(name, config)
    if named:
        prov, model = named
        if model is None:
            return llm.provider(prov, config=config, require_ready=require_ready)
    elif "/" not in name:
        return llm.provider(name, config=config, require_ready=require_ready)
    else:
        prov, model = name.split("/", 1)
    p = llm.provider(prov, config=config, require_ready=False)
    if p.model and _same(p, model, p.model):
        return llm.provider(prov, config=config, require_ready=require_ready)
    if not p.discover:
        raise llm.LLMConfigError(f"provider {prov} does not discover models; it uses the model set in llm.yaml "
                                 f"({llm.bare_model(p.model) or 'none set'}). /model lists the models you can choose.")
    found = listing(p)
    match = [m for m in found["models"] if _key(p, m["model"]) == _key(p, model)]
    if not match and not model.startswith("@"):                    # a Portkey model typed without its @slug/
        match = [m for m in found["models"] if _same(p, m["model"], model)]
        if len(match) > 1:
            raise llm.LLMConfigError(f"{len(match)} of {prov}'s providers offer {model}; /model lists them, choose one by its number")
    if not match and found["error"] and not found["models"]:         # no list read: say so, not that the model is missing
        raise llm.LLMConfigError(f"{prov}'s list of models could not be read, so {model} cannot be chosen now: {found['error']}. "
                                 f"Check that this environment reaches {p.base_url} (no network, a VPN or a proxy in the way).")
    if not match:
        raise llm.LLMConfigError(f"{prov} does not offer {model}: {found['error'] or OFFERED[p.discover]}. "
                                 f"/model lists the models you can choose.")
    q = _discovered(p, match[0])
    if require_ready and q.missing():
        raise llm.LLMConfigError(f"{q.label} is not ready: it needs {'; '.join(q.missing())}")
    if require_checked and q.cloud and not passed(q.choice):
        raise NotChecked(f"{q.label} is listed by {prov} but has not been checked yet; a listed cloud model can still refuse every "
                         f"call. `python -m automc models check {q.label} --tools` checks it once (a hello, one tool call, and "
                         f"the hello with AutoMC's tool definitions: about 6,000 tokens, fixed text only).")
    return q


# ---- checks that passed -------------------------------------------------------------------------------------------------------
def _checks_path():
    return automc_paths.state("models", "checks.json")


def _checks():
    try:
        return json.loads(_checks_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def passed(choice):
    """True when `choice` has passed a check with the tool round trip."""
    rec = _checks().get(choice) or {}
    return bool(rec.get("ok") and rec.get("tools"))


def record_check(choice, out, tools):
    """Keep the outcome of a real check (models.check): passed or not, with the tool round trip or not, when. Nothing else."""
    path = _checks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _checks()
    data[choice] = {"ok": bool(out.get("ok")), "tools": bool(tools), "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "seconds": out.get("seconds"), "tool_seconds": out.get("tool_seconds")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
