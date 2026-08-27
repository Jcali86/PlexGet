"""What a provider is, and how to add another one.

A provider does one thing: it is handed a system prompt, a message and a
pydantic model, and it fills the model in. Not a chat interface, not a general
client - both places this app talks to a model want a filled-in form back, and
keeping the shape that narrow is what keeps a second provider down to one small
file.

The other half of the bargain is that it never raises. Every way a model call
can go wrong - a timeout, a refusal, output that will not parse, the service
being down, the SDK never installed - reaches the caller as None, because every
caller answers all of them the same way: by falling back to the keyword rules.
Those are a poorer answer than a good one and a far better answer than an
error. An adapter that lets an exception out turns a slow afternoon at the
provider into a broken search.

Adding a provider is one file and one word of config. The stub at the foot of
this module is the whole shape; nothing here keeps a registry, so there is no
list to add a name to.
"""


class Provider:
    """The interface every adapter implements. Not usable on its own."""

    # The importable name of the SDK this adapter needs, when it needs one.
    # Checked before the adapter is handed out, so a provider named in config
    # whose library was never installed reads as "no model configured" and
    # falls back to keywords, rather than failing on the first search somebody
    # runs.
    sdk = ""

    def __init__(self, settings):
        # The ai block from config, with the defaults filled in and the API key
        # already resolved - the adapter does not need to know that the key may
        # have come from the environment.
        self.settings = settings

    def structured(self, system, prompt, schema, max_tokens=None):
        """Fill in `schema` from `prompt`, or return None.

        `system` is the character and the rules, `prompt` the thing somebody
        typed, `schema` a pydantic model class. The return is an instance of
        that class or None, and None is not an error to be reported: it is the
        ordinary answer for anything short of success.

        `max_tokens` is a ceiling, not a spend; left out, the configured one
        applies. Callers pass every argument by keyword, so the order here is
        not something anybody depends on.
        """
        raise NotImplementedError


# ---- adding a provider ------------------------------------------------------
#
# Write api/ai/<name>_provider.py holding a class called Provider, then put
# `provider: <name>` in the ai block of config. That is the whole procedure -
# the loader finds the file by name and reads .Provider off it, so no other
# file changes.
#
# What follows is an OpenAI-compatible adapter, left commented because an
# untested provider in the tree is worse than none: it reads as supported and
# then falls over in front of somebody who is only trying to watch a film. Copy
# it, finish it, and try it against a real key before trusting it.
#
# Note where the SDK import sits. Importing it inside the method keeps a
# machine that never installed it starting normally, and `sdk` above is what
# lets the loader decline the adapter before anybody waits on a call that
# cannot work.
#
# from api.ai import base
#
#
# class Provider(base.Provider):
#     """OpenAI, and anything speaking its API - a gateway, or something local."""
#
#     sdk = "openai"
#
#     def structured(self, system, prompt, schema, max_tokens=None):
#         import openai
#
#         conf = self.settings
#         try:
#             client = openai.OpenAI(
#                 api_key=conf["api_key"],
#                 timeout=conf["timeout"],
#                 max_retries=1,
#                 # Only when config points somewhere other than the provider's
#                 # own address; passing an empty one is not the same as passing
#                 # none, and some clients take it literally.
#                 **({"base_url": conf["base_url"]} if conf["base_url"] else {}),
#             )
#             reply = client.responses.parse(
#                 model=conf["model"],
#                 max_output_tokens=max_tokens or conf["max_tokens"],
#                 instructions=system,
#                 input=prompt,
#                 text_format=schema,
#             )
#         except Exception:
#             # Deliberately everything. The caller's fallback is the same
#             # whichever of these it was, and a raise from here takes down a
#             # search that had a perfectly good keyword answer waiting.
#             return None
#         # A refusal fills in nothing, which is a None the caller already
#         # handles - so it needs no special case here.
#         return reply.output_parsed
