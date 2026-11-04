"""
The agent's language models: read config/llm.yaml, check each provider, say what a provider still needs, and make the LangChain chat
model for it. The tools never see this module; they stay plain functions (tools/registry.py).

Keys are read only from the variable a provider names (api_key_env), in the shell or in the keys file AUTOMC_ENV_FILE names
(agent/keys.py); they are never written to a file, an audit or a message, and describe() never shows them.

How each backend is called follows what RADIANT-LLM verified against the live services (radiant_llm.py, 2026-10-22): Gemini through
langchain-google-genai (Google's own API, which carries Gemini 3's thought signatures between tool calls), not its OpenAI endpoint;
the GPT families that take tools only through OpenAI's Responses API (gpt-5.4 and later 5.x, gpt-6) with use_responses_api; usage
reported while streaming, so that the agent can count tokens (AutoMC_Agent_Models_Plan.md, MS-4).
"""
from __future__ import annotations

import importlib.util
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import automc_paths
from agent import keys

CONFIG_PATH = automc_paths.SRC / "config" / "llm.yaml"
KINDS = ("local", "cloud")
ACCOUNTS = ("self_hosted", "enterprise", "personal")
BACKENDS = {"ollama": "langchain_ollama", "openai_compatible": "langchain_openai", "anthropic": "langchain_anthropic",
            "google_genai": "langchain_google_genai"}
DISCOVER = {"ollama": "ollama", "openai": "openai_compatible", "gemini": "google_genai"}   # agent/discovery.py: family -> backend
_KEYS = {"kind", "account", "backend", "base_url", "model", "num_ctx", "temperature", "reasoning", "timeout_s", "api_key_env", "headers",
         "max_output_tokens", "context_window", "discover", "max_tokens_per_session"}
POLICY_ENV = "AUTOMC_ALLOW_RESTRICTED_ON_CLOUD"
OLLAMA_ENV = "OLLAMA_HOST"                # when set, the address of every ollama provider (llm.yaml's base_url otherwise)
CLOUD_CAP = 200_000                       # tokens per conversation for a cloud provider unless llm.yaml says otherwise (D3)
MAX_RETRIES = 2
# RADIANT-LLM's verified profiles (utils/cloud_model_catalog.py): these GPT families take tools only through the Responses API.
RESPONSES_API = re.compile(r"^gpt-(?:5\.(?:[4-9]|\d{2,})|6)(?:[.-]|$)")
# Who serves a model, for its label: by backend, else by the address's host.
SERVICES = {"ollama": "ollama", "google_genai": "gemini", "anthropic": "claude"}
HOSTS = (("portkey.ai", "portkey"), ("openai.com", "openai"))
_SLUG = re.compile(r"@[^/\s]+/")


class LLMConfigError(Exception):
    pass


def bare_model(model):
    """gpt-5.4 for a Portkey id @slug/gpt-5.4, else the model as it is."""
    return model.split("/", 1)[1] if model.startswith("@") and "/" in model else model


def screen_name(text):
    """Any text with a Portkey @slug/ taken out, for a name known only as text (AUTOMC_LLM_PROVIDER, an old choice)."""
    return _SLUG.sub("", text or "")


@dataclass
class Provider:
    name: str
    kind: str
    backend: str
    base_url: str
    model: str
    account: str = "self_hosted"
    temperature: float | None = 0.0
    num_ctx: int | None = None
    reasoning: bool | None = None
    timeout_s: float = 600.0
    api_key_env: str | None = None
    headers: dict = field(default_factory=dict)
    max_output_tokens: int | None = None
    context_window: int | None = None
    discover: str | None = None           # ollama, openai or gemini: list this service's models (agent/discovery.py)
    discovered: bool = False              # this model came from that list, not from llm.yaml
    max_tokens_per_session: int | None = None

    @property
    def cap(self):
        """Tokens one conversation may spend on this provider: max_tokens_per_session, else 200,000 for a cloud provider, else none."""
        return self.max_tokens_per_session or (CLOUD_CAP if self.cloud else None)

    @property
    def responses_api(self):
        """True for a GPT family that takes tools only through OpenAI's Responses API (the model name without a Portkey @slug/)."""
        return self.backend == "openai_compatible" and bool(RESPONSES_API.match(bare_model(self.model)))

    @property
    def choice(self):
        """The model's full name inside AutoMC (checks, audits): the provider's name, or PROVIDER/MODEL for a discovered one. A Portkey
        model's carries the workspace's @slug/, which names the account: never shown on screen (label is)."""
        return f"{self.name}/{self.model}" if self.discovered else self.name

    @property
    def service(self):
        """Who serves the model: ollama, portkey, openai, gemini or claude, from the backend, else the address, else the provider's
        name (openai-portkey: portkey); any other OpenAI-style service goes by the provider's name."""
        if self.backend in SERVICES:
            return SERVICES[self.backend]
        host = (urllib.parse.urlparse(self.base_url).hostname or "").lower()
        return next((s for end, s in HOSTS if host == end or host.endswith("." + end)), None) or next(
            (s for _, s in HOSTS if s in self.name.lower()), self.name)

    @property
    def label(self):
        """What the screen shows and what the user may type back: KIND-SERVICE/MODEL, such as cloud-portkey/gpt-5.4 or
        local-ollama/qwen3.6:35b-32k-t0, without a Portkey @slug/ and without Ollama's :latest."""
        model = bare_model(self.model)
        if self.backend == "ollama" and model.endswith(":latest"):
            model = model[:-len(":latest")]
        return f"{self.kind}-{self.service}/{model or 'no model set'}"

    @property
    def window(self):
        """Tokens the model can hold, if known (the context guard's limit): context_window, else num_ctx, else None."""
        return int(self.context_window or self.num_ctx or 0) or None

    @property
    def cloud(self):
        return self.kind == "cloud"

    def describe(self):
        """For the screen: the label, the account and the provider in llm.yaml; never the key, the headers' values or a Portkey
        @slug/ (full_name() has that, for the audit)."""
        return f"{self.label} ({self.account} account; provider {self.name} in llm.yaml)"

    def full_name(self):
        """For the audit only: provider, backend, the full model id and the address."""
        return f"{self.name} ({self.kind}, {self.account} account, {self.backend}: {self.model or 'no model set'} at {self.base_url or 'none'})"

    def key(self):
        return keys.value(self.api_key_env)

    def missing(self):
        """What this provider still needs before it can be used, as plain phrases (empty when it is ready)."""
        out = []
        if not self.model:
            out.append("a model name (model in llm.yaml)")
        if not self.base_url:
            out.append("an address (base_url in llm.yaml)")
        if self.api_key_env and not self.key():
            out.append(f"a key in {self.api_key_env} (in the shell, or in the keys file {keys.ENV_VAR} names)")
        if importlib.util.find_spec(BACKENDS[self.backend]) is None:
            out.append(f"the Python package {BACKENDS[self.backend].replace('_', '-')} (pip install it in the automc environment)")
        return out


def load_config(path=None):
    import yaml
    path = Path(path or CONFIG_PATH)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise LLMConfigError(f"cannot read {path}: {e}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict) or not raw["providers"]:
        raise LLMConfigError(f"{path} needs a providers mapping")
    return raw


def _make(name, p):
    if not isinstance(p, dict):
        raise LLMConfigError(f"provider {name}: must be a mapping")
    unknown = sorted(set(p) - _KEYS)
    if unknown:
        raise LLMConfigError(f"provider {name}: unknown keys {unknown}; allowed: {sorted(_KEYS)}")
    if p.get("kind") not in KINDS:
        raise LLMConfigError(f"provider {name}: kind must be local or cloud (it decides who may see restricted data)")
    account = p.get("account", "self_hosted" if p.get("kind") == "local" else None)
    if account not in ACCOUNTS:
        raise LLMConfigError(f"provider {name}: account must be one of {ACCOUNTS}")
    if (p["kind"] == "local") != (account == "self_hosted"):
        raise LLMConfigError(f"provider {name}: a local provider is self_hosted, and a cloud provider never is")
    if p.get("backend") not in BACKENDS:
        raise LLMConfigError(f"provider {name}: backend must be one of {sorted(BACKENDS)}")
    if p["kind"] == "cloud" and not p.get("api_key_env"):
        raise LLMConfigError(f"provider {name}: a cloud provider needs api_key_env, the name of the variable that holds its key")
    if p["backend"] in ("anthropic", "google_genai") and p["kind"] != "cloud":
        raise LLMConfigError(f"provider {name}: backend {p['backend']} is a cloud service")
    cap = p.get("max_tokens_per_session")
    if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0):
        raise LLMConfigError(f"provider {name}: max_tokens_per_session must be a positive number of tokens")
    headers = p.get("headers") or {}
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise LLMConfigError(f"provider {name}: headers must map names to text")
    if any(k.lower() in ("authorization", "x-api-key", "x-portkey-api-key", "api-key", "x-goog-api-key") for k in headers):
        raise LLMConfigError(f"provider {name}: keys never go in headers in llm.yaml; name the variable in api_key_env")
    discover = p.get("discover") or None
    if discover is not None and discover not in DISCOVER:
        raise LLMConfigError(f"provider {name}: discover must be one of {sorted(DISCOVER)} (no other service is discovered)")
    if discover and (DISCOVER[discover] != p["backend"] or (discover == "ollama") != (p["kind"] == "local")):
        raise LLMConfigError(f"provider {name}: discover: {discover} needs backend {DISCOVER[discover]} and a "
                             f"{'local' if discover == 'ollama' else 'cloud'} provider")
    base_url = p.get("base_url") or ""
    if p["backend"] == "ollama" and os.environ.get(OLLAMA_ENV, "").strip():     # another Ollama, as Ollama's own clients read it
        host = os.environ[OLLAMA_ENV].strip().rstrip("/")                         # (a container reaches the host's: docker-compose.yml)
        base_url = host if "://" in host else f"http://{host}"
    return Provider(name=name, kind=p["kind"], account=account, backend=p["backend"], base_url=base_url,
                    model=p.get("model") or "", temperature=p.get("temperature"), num_ctx=p.get("num_ctx"), reasoning=p.get("reasoning"),
                    timeout_s=float(p.get("timeout_s", 600)), api_key_env=p.get("api_key_env"), headers=dict(headers),
                    max_output_tokens=p.get("max_output_tokens"), context_window=p.get("context_window"), discover=discover,
                    max_tokens_per_session=cap)


def providers(config=None):
    """Every provider in llm.yaml, checked (a malformed entry is an error; an incomplete one is listed with what it misses)."""
    config = config or load_config()
    return [_make(name, p) for name, p in config["providers"].items()]


def policy(config=None):
    """The policy section: {restricted_allowed_on: [provider names]}."""
    config = config or load_config()
    pol = config.get("policy") or {}
    if not isinstance(pol, dict) or set(pol) - {"restricted_allowed_on"}:
        raise LLMConfigError("policy in llm.yaml may hold only restricted_allowed_on")
    names = pol.get("restricted_allowed_on") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise LLMConfigError("policy.restricted_allowed_on must be a list of provider names")
    return {"restricted_allowed_on": names}


def restricted_allowed(p, config=None, environ=None):
    """True only if the data owners' decision is recorded twice: the provider is named in policy.restricted_allowed_on AND the
    environment variable AUTOMC_ALLOW_RESTRICTED_ON_CLOUD is 1. Always false for a personal account; always true for a local model."""
    if not p.cloud:
        return True
    if p.account == "personal":
        return False
    environ = os.environ if environ is None else environ
    return p.name in policy(config)["restricted_allowed_on"] and environ.get(POLICY_ENV) == "1"


def default_name(config=None):
    """The model a chat starts with: AUTOMC_LLM_PROVIDER (the shell, else the keys file: one uncommented line switches it), else
    default_provider in llm.yaml."""
    config = config or load_config()
    return keys.value("AUTOMC_LLM_PROVIDER") or config.get("default_provider")


def provider(name=None, config=None, require_ready=True):
    """The named provider (default: AUTOMC_LLM_PROVIDER, else default_provider). Refused, with what it misses, if it is not ready."""
    config = config or load_config()
    name = name or default_name(config)
    if name not in config["providers"]:
        raise LLMConfigError(f"no provider {name!r} in llm.yaml; providers: {sorted(config['providers'])}")
    p = _make(name, config["providers"][name])
    if require_ready and p.missing():
        raise LLMConfigError(f"provider {name} is not ready: it needs {'; '.join(p.missing())}")
    return p


def chat_model(p):
    """The LangChain chat model of a provider. Raises LLMConfigError if the provider is not ready."""
    if p.missing():
        raise LLMConfigError(f"provider {p.name} is not ready: it needs {'; '.join(p.missing())}")
    if p.backend == "ollama":
        from langchain_ollama import ChatOllama
        kw = {"model": p.model, "base_url": p.base_url, "client_kwargs": {"timeout": p.timeout_s, "headers": dict(p.headers)}}
        if p.temperature is not None:
            kw["temperature"] = p.temperature
        if p.num_ctx:
            kw["num_ctx"] = int(p.num_ctx)
        if p.reasoning is not None:
            kw["reasoning"] = bool(p.reasoning)
        if p.max_output_tokens:
            kw["num_predict"] = int(p.max_output_tokens)
        return ChatOllama(**kw)
    if p.backend == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kw = {"model": p.model, "api_key": p.key(), "base_url": p.base_url, "timeout": p.timeout_s,
              "max_tokens": int(p.max_output_tokens or 4000), "default_headers": dict(p.headers), "max_retries": MAX_RETRIES}
        if p.temperature is not None:
            kw["temperature"] = p.temperature
        return ChatAnthropic(**kw)
    if p.backend == "google_genai":
        from langchain_google_genai import ChatGoogleGenerativeAI
        kw = {"model": p.model, "google_api_key": p.key(), "timeout": p.timeout_s, "max_retries": MAX_RETRIES,
              "model_kwargs": {"stream_usage": True}}         # through model_kwargs in 4.x, as RADIANT-LLM found
        if p.headers:
            kw["additional_headers"] = dict(p.headers)
        if p.temperature is not None:
            kw["temperature"] = p.temperature
        if p.max_output_tokens:
            kw["max_output_tokens"] = int(p.max_output_tokens)
        return ChatGoogleGenerativeAI(**kw)
    from langchain_openai import ChatOpenAI
    kw = {"model": p.model, "base_url": p.base_url, "api_key": p.key() or "none", "timeout": p.timeout_s,
          "default_headers": dict(p.headers) or None, "max_retries": MAX_RETRIES, "stream_usage": True}
    if p.responses_api:
        kw["use_responses_api"] = True
    if p.temperature is not None:
        kw["temperature"] = p.temperature
    if p.max_output_tokens:
        kw["max_tokens"] = int(p.max_output_tokens)
    return ChatOpenAI(**kw)


def content_text(content):
    """The text of a model's reply: the string, or the text blocks of a list (the Responses API and Gemini reply in blocks, with
    reasoning and signatures beside the text)."""
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, str):
            out.append(b)
        elif isinstance(b, dict) and b.get("type") in ("text", "output_text") and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "".join(out)
