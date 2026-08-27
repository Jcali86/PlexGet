"""Anthropic, the one provider written so far.

Structured output is the whole reason this is the first one: the SDK takes the
pydantic model straight and hands back an instance of it, so nothing here has
to coax JSON out of prose and then hope it parses.
"""

from api.ai import base


class Provider(base.Provider):
    """Claude, through the official SDK."""

    sdk = "anthropic"

    def structured(self, system, prompt, schema, max_tokens=None):
        # Imported here rather than at the top of the file so a machine that
        # never installed the SDK still starts, and still answers with the
        # keyword rules.
        import anthropic

        conf = self.settings
        try:
            client = anthropic.Anthropic(
                api_key=conf["api_key"],
                timeout=conf["timeout"],
                # One retry and no more. A second one costs longer than the
                # keyword fallback takes to answer, and the person is waiting.
                max_retries=1,
                # Only when config points somewhere other than Anthropic's own
                # address; an empty base_url is not the same as no base_url,
                # and the client takes it literally.
                **({"base_url": conf["base_url"]} if conf["base_url"] else {}),
            )
            reply = client.messages.parse(
                model=conf["model"],
                max_tokens=max_tokens or conf["max_tokens"],
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            # A refusal, or output that would not parse, arrives as None
            # already - which is exactly what the caller is expecting, so it
            # needs no special case.
            return reply.parsed_output
        except Exception:
            # Deliberately everything: a timeout, a dead service, an expired
            # key, an SDK that moved on. The caller answers all of them the
            # same way, and a raise from here would take down a search that had
            # a perfectly good keyword answer waiting behind it.
            return None
