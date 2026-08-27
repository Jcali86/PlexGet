# PlexGet

A request page for the people you share your Plex server with.

Somebody opens the link on their phone, types what they fancy — a title, a
director, or "something gentle after a long week" — and gets films from *your*
library, ready to drop into a playlist in their own Plex account. Anything you
do not have becomes an entry on a wanted list, and when it finally turns up
they are told.

It runs on your own machine, next to Plex. There is no service behind it and no
account to create: people sign in with the same Plex account you already shared
the library with, and if you have not shared with them, they do not get in.

---

## Screenshots

None here yet, on purpose — the page is skinned and named by you, and a set of
someone else's screenshots would be showing you a different app to the one you
are about to run.

Put your own in `docs/screenshots/` and link them from this section. Worth
capturing: the sign-in, the search results, a playlist, and the wanted list on
a phone.

---

## What it does

### For the household

Someone asks for something and picks from what turns up.

A title, an actor or a director is a straight library lookup and costs nothing.
Anything vaguer goes to a language model, which turns the phrase into *filters*
— genres, a rating floor, a year range — and never into film titles. That is
the whole trick: an invented title is a film you do not own, whereas a filter
can only ever select from what is actually on the shelves.

Without a model key it still works. A keyword map handles "romcom" and "90s
action"; it does not handle "something gentle after a long week".

What they get:

- **Films in their own playlist.** Created with their Plex token, so it belongs
  to them and appears in their Plex app, not in yours.
- **A wanted list.** Anything the library lacks can be asked for. The title is
  identified against TMDb first, so the entry reads *Taxi (1998)* rather than
  "taxi, the french one", and is something you can actually go and find.
- **Series as well as films**, whole or by the season.
- **Notifications when it arrives**, by web push — no app store involved. Since
  iOS 16.4 a page added to the home screen can receive them, and the same code
  covers Android and desktop.
- **A home-screen app.** The manifest and service worker make it installable,
  and the installed copy opens already signed in rather than asking a second
  time for one phone.
- **Suggestions from what they have already watched**, drawn from Plex history
  and never proposing something that would have to be fetched.
- **Notes from you**, if you post one, and a skin picker for people with
  opinions about how it looks.

### For you

- **The wanted list**, with who asked and when. Plex's `library.new` webhook
  settles entries the moment the file lands and tells whoever asked. Webhooks
  are a Plex Pass feature; without one, `POST /wanted/recheck` does the same
  job on a schedule.
- **A push when somebody asks for something**, and another when a search turns
  up a film Plex lists but can no longer play — a file that has quietly gone
  missing puts itself back on the wanted list.
- **An owner console** at `/dashboard`: overview, upgrade priority, gaps,
  poster status, wanted, export.
- **Library maintenance tooling** — a file scanner, an ffprobe quality audit, a
  TMDb gap analysis, a filename auditor and a poster pipeline. This is the
  older half of the project and it assumes the machine running it can see the
  library files themselves. The request page needs none of it: that reads Plex
  live, over the network, and does not care where the files are.

---

## What it does not do

Worth knowing before you clone it.

- **It does not download anything.** Nothing here fetches media, ever. A
  request is a line on a list; acquiring it stays your job. The Sonarr and
  Radarr endpoints are read-only listings, not a queue.
- **It does not play anything.** Plex does that. This is the asking, not the
  watching.
- **One Plex server.** There is no notion of a second one.
- **No accounts of its own.** Plex is the only sign-in, which also means you
  cannot let in somebody you have not shared the library with.
- **No settings screen.** Configuration is `config.yaml` and a restart.
- **It is not hardened for hostile traffic.** It is built for a household —
  people you already trusted with your library. The rate limits exist to keep a
  model bill down, not to hold off an attacker. Read `SECURITY.md` before you
  put it on the internet.
- **SQLite and a single process.** Fine for a dozen people, not for a hundred.
- **One AI provider is implemented.** Anthropic. The seam is there for others;
  nobody has written one yet.
- **macOS and Linux.** Windows is untested, and parts of the maintenance
  tooling are frankly macOS-shaped.

---

## Requirements

| | |
|---|---|
| Plex Media Server | Running, with at least one film or TV library, and an account that owns it |
| Python | 3.11 or newer |
| A Plex token | Yours, the server owner's — see below |
| TMDb API key | Optional but recommended. Free. Without it, requests for films you do not own cannot be identified properly |
| An AI provider key | Optional. Without it, free-form requests fall back to keyword matching |
| ffmpeg | Optional, and only for the quality audit, which shells out to `ffprobe` |

For notifications and add-to-home-screen the page has to be served over HTTPS.
`localhost` counts as secure, so both work while you are trying it out on the
machine itself; a phone on your network reaching it over plain HTTP will not
see either.

---

## Setup

**1. Clone it and make a virtual environment.**

```bash
git clone <your-fork-or-this-repo> plexget
cd plexget
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Copy the example config.**

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is gitignored, and it is about to hold a token that is worth as
much as your Plex password. Keep it that way.

**3. Find your Plex token.**

In Plex Web, open any item, choose **Get Info**, then **View XML**. The URL of
the page that opens ends in `?X-Plex-Token=...`. That value is yours.

It is an admin token for your server: anyone holding it can read your library,
your watch history and your account. Do not paste it into an issue, a gist or a
screenshot.

**4. Point it at Plex.**

```yaml
plex:
  url: "http://localhost:32400"
  token: "the token you just found"
```

If Plex is on another machine, give its address instead. The request page only
ever talks to Plex over the network, so it does not need to see the files.

**5. Add a TMDb key, if you want the "we do not have that" path to work
properly.** Register at themoviedb.org, take the v3 API key, and put it in the
`tmdb` block. It is used to turn a half-remembered title into a real one, and
to check whether a film is even out yet — there is no point offering to add
something that is still in cinemas.

**6. Add a model key, if you want moods understood.**

The key is read from the environment, so it never sits in a file:

```bash
export ANTHROPIC_API_KEY="..."
```

The `ai` block in `config.yaml` says which provider and model to use and what
environment variable to read. Leave the whole block alone and the page still
works — it just answers "something gentle after a long week" with a shrug.

**7. Check the library paths, or delete them.** The `nas` block is only read by
the maintenance tooling, which walks the files on disk. If you are only running
the request page, it is dead weight and can stay as it is.

---

## Running it

```bash
source venv/bin/activate
python main.py
```

Then open <http://localhost:5050/> — the request page — and
<http://localhost:5050/dashboard> for the owner console. The port comes from
the `flask` block.

Two things worth checking straight away:

```bash
curl -s localhost:5050/health
curl -s localhost:5050/libraries   # loopback only, so this works from the machine itself
```

If `/libraries` lists your Plex sections, everything downstream of it works.

The database is created on first use. There is nothing to migrate and nothing
to seed: if the file named in `database.path` is missing it is built empty,
and the request page fills in what it needs as it goes.

For running it permanently — systemd, launchd, Docker — see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Letting people in from outside

The point of the thing is a link you can send to somebody who is not in the
house, so at some stage it has to be reachable. Three ways, none of them
special-cased in the code:

- **A reverse proxy you already run** — nginx, Caddy, Traefik — terminating
  TLS and passing through to the app.
- **Tailscale Funnel**, which publishes one port from a machine on your tailnet
  without touching your router.
- **A Cloudflare Tunnel**, likewise, if you already have a domain there.

Whichever you pick, three rules:

1. **Serve it over HTTPS.** Push notifications and home-screen install both
   require it, and everybody's Plex token is going over that connection.
2. **Bind the app to loopback** (`flask.host: "127.0.0.1"`) and let the proxy
   be the only thing that can reach it.
3. **Make sure your proxy sets `X-Forwarded-For`.** This one matters more than
   it looks — see below.

---

## Who can see what

Every request falls into one of three tiers, decided in one place before
anything else runs. Nothing is reachable unless it is named, so an endpoint
nobody remembered is closed by default rather than open by accident.

| Tier | Who | What |
|---|---|---|
| Open | Anybody with the link | The page shell, `/health`, the manifest, the service worker, the persona, the icons, and the sign-in itself |
| Household | A Plex account this server is shared with | Everything under `/request/` and `/push/`, and library artwork |
| Owner | You | Everything else: scans, the fixer, poster tools, exports, announcements, the dashboard |

Roles come from Plex, not from a list you maintain. The account that owns the
server is the owner; the accounts you have shared *this* server with are the
household. If Plex cannot be asked, nobody is let in on the strength of a
guess — with no list, only the owner's own token works.

Sessions last 90 days. Each person's Plex token is kept because acting on their
account needs the real thing rather than a hash of it, and it is encrypted at
rest with a key in `data/`, outside version control and readable only by the
user running the app.

**The owner tier has a second door**, and it is the one deployment detail most
likely to bite you: a request that arrives with no `X-Forwarded-For` header
from `127.0.0.1` is treated as the owner. That keeps the console usable from a
browser on the machine itself without a sign-in. A reverse proxy on that same
machine also connects from `127.0.0.1` — so if it does not set
`X-Forwarded-For`, every visitor on the internet becomes the owner. Check it
before you publish the host, not after.

---

## Making it yours

Everything in this section lives in `config.yaml`. All of it is optional, and
leaving it out gets you a plain, neutral version rather than an error.

**The name.** `app.name` is the page title and the name of the installed
home-screen app. Each person's default playlist is named after them, not after
the app.

**The assistant.** The thing that answers requests has a name, a greeting, a
voice, and a set of lines for people trying to talk it into writing their
homework. Give it artwork if you like — drop the files in `dashboard/icons`,
name them in the `persona.images` block, and they are drawn beside what it
says. Every layout reads properly with none of them, so one or two is a
perfectly good place to start.

[personas/README.md](personas/README.md) walks through it with worked examples.

The parts that are *not* configurable are deliberate: what it is allowed to
answer, the filters it has to fill in, and the rule that a request is data
rather than instruction are all fixed in code. A persona author can change how
it sounds, never what it may do.

**What it sends.** The `notifications` block holds every message this app is
capable of sending — four of them. Reword any of them, or turn them off.
Nothing else is ever sent.

**What it may spend.** `app.limits` caps model use per person per hour and per
day, and the whole server per day, so no combination of people can run up a
bill overnight.

**How it looks.** A few skins ship with it; the picker sits on the page and the
choice is remembered per device.

---

## Adding an AI provider

Anthropic is the only adapter written, but it is not wired in anywhere special.
A provider is one file:

```
api/ai/<name>_provider.py
```

holding a class called `Provider` that inherits from `api/ai/base.py` and
implements one method:

```python
def structured(self, system, prompt, schema, max_tokens=None) -> BaseModel | None
```

It fills in `schema` and returns an instance of it, or `None`. `None` covers
every way this can fail — a timeout, a refusal, output that will not parse, the
service being down — because every caller answers all of them the same way, by
falling back to keyword matching. **An adapter must never raise.**

Then point at it:

```yaml
ai:
  provider: "<name>"
  model: "whatever that provider calls the model you want"
  api_key_env: "THEIR_API_KEY"
```

There is no registry to edit; the module is found by name. Read the note at the
top of `api/ai/base.py`, and see [CONTRIBUTING.md](CONTRIBUTING.md) before
sending a pull request.

Quick matters more than clever here. This call holds up a search somebody is
waiting on, and a slow answer is beaten by the keyword fallback that arrives.

---

## Layout

```
config.example.yaml   copy to config.yaml
config.py             loads it
main.py               Flask entrypoint
api/
  routes.py           every endpoint, and the three-tier guard
  plex_auth.py        sign in with Plex, sessions, who is allowed
  persona.py          the assistant's name, voice and artwork
  ai/                 provider adapters; base.py is the contract
  request_assistant.py plain English in, filters out, films back
  suggestions.py      picks drawn from what somebody has watched
  user_library.py     acting on a person's own Plex account
  limits.py           model allowances
  push.py             web push
  tmdb_lookup.py      naming a film the library does not have
  secrets_store.py    encryption for stored Plex tokens
scanner/              the maintenance half: files, quality, gaps, names
analyzer/             ffprobe and TMDb helpers
db/database.py        SQLite schema and queries
dashboard/
  request.html        the household page
  index.html          the owner console
  sw.js               service worker
  icons/              app icons and persona artwork
scripts/              one-off jobs, all of them safe to re-run
docs/                 deployment notes
```

`data/` and `*.db` appear on first run and are gitignored. `data/` holds the
encryption key and the push identity; back it up, and never commit it.

---

## Licence and the rest

MIT — see [LICENSE](LICENSE). Copyright the PlexGet contributors.

- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and house style
- [SECURITY.md](SECURITY.md) — reporting a problem, and what to check before
  you publish the host
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — systemd, launchd, Docker, proxies

Not affiliated with Plex, TMDb, or anyone else named here. It is a program that
asks your Plex server questions on behalf of people you already trust.
