# Contributing

Patches welcome. It is a small project with a narrow job, so the most useful
thing you can do before writing anything is read `api/routes.py` and decide
whether what you have in mind belongs in it.

---

## Getting set up

```bash
git clone <your fork> plexget
cd plexget
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Fill in `plex.url` and `plex.token` and you have a working app. Everything else
in the config is optional, and quite a lot of the interesting behaviour is on
the paths where a key is *missing* — no model, no TMDb key, no persona artwork
— so it is worth developing without them at least some of the time. Those paths
are the ones a stranger hits first.

Run it in the foreground:

```bash
python main.py
```

Set `flask.debug: true` in your own `config.yaml` for the reloader while you
work. Never on anything reachable from outside: the debugger is a remote shell
by design.

### Signing in as somebody else

The three access tiers are the thing most easily broken by accident, and the
only honest way to test the household tier is to sign in as a Plex account that
the server is shared with but does not own. A second Plex account costs
nothing. The owner tier is reachable from a browser on the machine itself, so
it is easy to convince yourself a change works when it works only for you.

### Tests

There are none, which is the plain truth rather than an invitation to leave it
that way. Until there are, say in the pull request what you actually exercised.
The paths worth walking by hand after almost any change:

- A title search, an actor search and a mood search.
- A request for something the library does not have.
- Adding films to a playlist, and checking they land in *that person's* Plex.
- The page signed out, signed in as household, and signed in as owner.
- A cold load with the service worker already installed — the shell is cached,
  so a change to `request.html` may not be what you are looking at.

---

## House style

The code reads like prose written by somebody in a hurry but not careless.
Match it rather than improving on it.

**Comments explain why, not what.** The code already says what it does. A
comment earns its place by recording the thing that is not visible: the Plex
behaviour that forced this shape, the failure that made this a two-step, the
reason the obvious version was wrong. If a comment restates the line below it,
delete the comment.

**Plain British prose, in sentences.** No bullet lists inside code comments, no
headings, no boxes of dashes. Lowercase-ish, unfussy, and dry. Say "colour",
"behaviour", "recognise".

**Never write "we" or "I" in a comment.** The comments describe the program and
its constraints, not the people who wrote it. "The token is kept because a hash
cannot sign a request", not "we keep the token".

**Docstrings are one line where one line does.** A longer one is for the case
where the *reason* takes a paragraph, which is often.

**Keep the diff about one thing.** A rename buried inside a behaviour change is
how a bug survives review.

There is no formatter and no linter. Four-space indent, sensible line lengths,
and whatever the surrounding file is already doing.

---

## Things that are deliberately not configurable

Before adding a config key, check it is not on this list. Each is fixed in code
on purpose, and a pull request that lifts one into `config.yaml` will be turned
down:

- the off-topic patterns, and what happens when one matches
- the filter schema handed to the model, and its field descriptions
- the age-rating ladder
- the keyword fallback rules
- the paragraph at the end of the system prompt saying a request is data rather
  than instruction, which always goes last

The persona controls how the assistant sounds. It does not control what the
assistant is allowed to do, and the ordering in `request_assistant.translate()`
is what keeps those two things apart. Leave the guard at the end.

The same instinct applies to the access guard in `api/routes.py`. It is written
so that anything not named is closed. Adding an endpoint means deciding which
tier it belongs to; if you find yourself adding a path to `OPEN_PATHS` to make
something work, that is the moment to stop and think rather than the moment to
carry on.

---

## Adding an AI provider

This is the contribution the project is most obviously missing, and it is
meant to be small. One file:

```
api/ai/<name>_provider.py
```

with a class called `Provider` inheriting `api.ai.base.Provider`. Read
`api/ai/base.py` first — the docstring at the top is the contract, and
`anthropic_provider.py` is the worked example.

What a reviewer will look for:

- **`structured()` returns an instance of `schema`, or `None`.** Nothing else.
- **It never raises.** A timeout, a refusal, unparseable output, the service
  being down — all of them are `None`, because every caller answers all of them
  the same way, by falling back to keyword matching. An exception escaping the
  adapter takes a search down that would otherwise have quietly degraded.
- **The SDK is imported inside the method**, so that installing this project
  does not require every provider's library.
- **`base_url` is honoured when set** and ignored when empty, so somebody can
  point the same adapter at a gateway or a local runtime.
- **No registry entry**, no new import in `__init__.py`, no `if provider ==`
  anywhere. The module is found by name. If your change needs a line elsewhere
  to work, the seam is wrong and it is worth saying so in the pull request.
- **Add the dependency to `requirements.txt`**, pinned, and say in the pull
  request how you tested it and against which model.

A provider that only ever returns `None` because the SDK is not installed is
correct behaviour, not a bug. The app is supposed to keep working.

---

## Personas and skins

Both are content rather than code, and both are welcome. A persona is a block
of YAML and, optionally, artwork in `dashboard/icons`; a skin is a CSS block.
Keep them neutral enough that a stranger would want to use them, and keep the
artwork yours to give away — do not send anything you do not hold the rights
to.

---

## Before you open a pull request

- `config.yaml`, `.env`, `data/` and `*.db` are gitignored. Check your diff
  anyway. A Plex token in a commit is a Plex token you have to rotate, and
  rewriting history does not un-publish it.
- No personal paths, hostnames, tailnet names or account names in code,
  comments, examples or defaults. This project was extracted from somebody's
  own setup once already; the whole point was to get that out of it.
- Say what you changed and why. The why is the part nobody can reconstruct
  later.
- For anything that changes the page, a screenshot saves a great deal of
  back-and-forth.

Security problems do not go in a pull request. See [SECURITY.md](SECURITY.md).
