"""The model behind free-form requests, whichever one that is.

Everything about it lives in the ai block of config: which adapter to load,
which model to name, where the key comes from. Nothing outside this package
knows the provider's name, so the two call sites read the same whether there is
a model configured or not - they ask for a provider, and answer None by falling
back to keywords.

Loading is by filename. `provider: anthropic` reads api/ai/anthropic_provider.py
and takes the class called Provider off it. There is no registry, so adding a
provider means adding a file and nothing else - see the note at the top of
api/ai/base.py.
"""

import importlib
import importlib.util
import os
import sys

from config import config

from api.ai.base import Provider

DEFAULTS = {
    "provider": "anthropic",

    # A quick model, deliberately: this call fills in a fixed fifteen-field
    # form, which the middle of a range does as well as the top of it and in a
    # fraction of the time - and the time is what matters, because a slow
    # answer here holds up a search somebody is waiting on. A cleverer, slower
    # model's long-tail latency ate the timeout, fell back to keywords anyway,
    # and the person's phone gave up on the connection first.
    "model": "claude-sonnet-5",

    "api_key_env": "ANTHROPIC_API_KEY",
    "api_key": "",
    "timeout": 15,
    "max_tokens": 8000,
    "base_url": "",
}

_settings = None
_provider = None
# None is a real answer here, so whether the adapter has been built already
# cannot be read off _provider.
_built = False
_said = set()


def _complain(message):
    """Say what is wrong with the ai config, once, and carry on regardless.

    A misconfigured provider is not fatal - the keyword rules answer the page
    perfectly well - but it is silent, and somebody who has just put a key in a
    file deserves to be told why nothing changed. Said once because this sits
    behind a request and would otherwise repeat on every search.
    """
    if message in _said:
        return
    _said.add(message)
    print(f"ai config: {message}", file=sys.stderr)


def settings():
    """The ai block, with every default filled in and the types sorted out."""
    global _settings
    if _settings is None:
        # An empty config file parses as nothing at all, and no ai block is a
        # perfectly ordinary way to run this - both mean the keyword rules.
        loaded = config if isinstance(config, dict) else {}
        block = loaded.get("ai")
        if not isinstance(block, dict):
            block = {}
        merged = dict(DEFAULTS)
        for key, default in DEFAULTS.items():
            value = block.get(key)
            if value is None:
                continue
            if isinstance(default, int):
                # A timeout somebody typed as "15" is still fifteen seconds,
                # and a nonsense one is not worth honouring over the default.
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    _complain(f"ai.{key} is not a number, so {default} is being used")
                    continue
                if value <= 0:
                    _complain(f"ai.{key} must be above zero, so {default} is being used")
                    continue
            else:
                value = str(value).strip()
                # Blanking out a name is not a choice of provider or model, it
                # is leaving the line alone.
                if not value and key in ("provider", "model", "api_key_env"):
                    continue
            merged[key] = value
        _settings = merged
    return _settings


def api_key():
    """The key, from the environment first and the config file second.

    The environment wins so the key need never sit in a file, which is the
    whole reason api_key_env exists.
    """
    conf = settings()
    key = os.environ.get(conf["api_key_env"], "").strip() or conf["api_key"].strip()
    # The example config ships a YOUR_SOMETHING placeholder, and a placeholder
    # is not a key - treating it as one produces a mystifying auth failure on
    # the first search instead of the keyword fallback somebody expected.
    return "" if key.startswith("YOUR_") else key


def has_api_key():
    """Is there a model to talk to at all?"""
    return bool(api_key())


def provider():
    """The configured adapter, built once and kept, or None.

    None covers every reason there is no model to call: no key, a provider
    named in config with no file behind it, an SDK that was never installed, an
    adapter that will not build. Callers test for None and fall back to
    keywords, so nothing here raises - a broken ai block must cost the page its
    cleverness, never its ability to answer.
    """
    global _provider, _built
    if _built:
        return _provider
    _built = True

    if not has_api_key():
        return None

    name = settings()["provider"]
    module_name = f"api.ai.{name}_provider"
    if importlib.util.find_spec(module_name) is None:
        _complain(f"ai.provider is {name!r}, but there is no api/ai/{name}_provider.py")
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception as problem:
        _complain(f"api/ai/{name}_provider.py would not import: {problem}")
        return None

    adapter = getattr(module, "Provider", None)
    if not (isinstance(adapter, type) and issubclass(adapter, Provider)):
        _complain(f"api/ai/{name}_provider.py has no class called Provider "
                  f"built on api.ai.base.Provider")
        return None
    # Checked before the adapter is handed out rather than at the first call,
    # because an adapter imports its own SDK lazily and would otherwise report
    # a missing library as an ordinary failed answer, over and over.
    if adapter.sdk and importlib.util.find_spec(adapter.sdk) is None:
        _complain(f"ai.provider is {name!r}, which needs the {adapter.sdk!r} "
                  f"package, and that is not installed")
        return None

    # The key is resolved here rather than in the adapter, so environment
    # against file is settled in one place and an adapter only ever sees a key.
    conf = dict(settings())
    conf["api_key"] = api_key()
    try:
        _provider = adapter(conf)
    except Exception as problem:
        _complain(f"the {name!r} provider would not start: {problem}")
        _provider = None
    return _provider
