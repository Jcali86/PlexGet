# Security

## Reporting a problem

Report it privately. On GitHub that is the **Security** tab, **Report a
vulnerability** — it opens a draft advisory only the maintainers can see. If
that is not enabled on the copy you are looking at, open an issue that says
nothing except that you have found a security problem and would like somewhere
private to describe it, and wait to be given one. Do not put the details in a
public issue, a pull request, or a discussion thread.

Useful in a report: what an attacker ends up able to do, the shortest sequence
of requests that gets there, and which access tier they started from.

This is a small project run by volunteers. There is no bounty and no promised
turnaround, but a real report will get a real answer. Only the latest commit on
the default branch is supported — there are no maintained release branches, and
"upgrade" means pulling and restarting.

---

## Before you put this on the internet

This app is *designed* to be reachable from outside the house. That is the
whole point of it, and it changes the stakes of an ordinary mistake. Read this
section before you publish the host, rather than after.

### The owner tier has a loopback door

A request arriving from `127.0.0.1` with no `X-Forwarded-For` header is treated
as the owner, with no sign-in. That is what keeps the console usable in a
browser on the machine itself.

A reverse proxy running on that same machine also connects from `127.0.0.1`.
**If your proxy does not set `X-Forwarded-For`, everybody on the internet
becomes the owner** — scans, exports, the fixer, the dashboard, the lot.

Every common proxy can do this and most do it by default, but "most" is not
"yours". Check it before you publish, from a device outside the house:

```
curl -s https://your-host/libraries
```

A list of your Plex sections means the header is not arriving. A `403` means it
is. Tunnels (Tailscale Funnel, Cloudflare Tunnel) set the header themselves;
hand-rolled nginx and Caddy configs are where this goes wrong.

### Serve it over HTTPS

Everybody's Plex token crosses that connection, in both directions. Push
notifications and add-to-home-screen also refuse to work without a secure
context, so this is not only a security matter — it is the difference between
the app working and half of it silently not.

### Never run with the debugger on

`flask.debug: true` gives anyone who can provoke a traceback an interactive
Python console in the browser. It is a remote shell, not a convenience. Keep it
for a laptop and never for anything published.

### Bind to loopback

`flask.host: "127.0.0.1"` and let the proxy be the only route in. Binding
`0.0.0.0` puts the app on every network the machine is attached to, which is
one config mistake away from being the internet.

---

## Secrets, and where they live

| File | Holds | Never |
|---|---|---|
| `config.yaml` | Your Plex admin token, TMDb and any other API keys | Commit it. Paste it in an issue. Put it in a screenshot |
| `.env` | Whatever you keep there instead | Commit it |
| `data/secret.key` | The key encrypting every household member's stored Plex token | Commit it. Copy it off the machine |
| `data/vapid.json` | The push identity for this install | Commit it |
| `*.db` | Sessions, the wanted list, usage counters | Commit it |

All of them are in `.gitignore` already. Check your diffs anyway, especially if
you have forked and renamed things, and doubly so before making a repository
public that was private while you were experimenting.

**A commit is published the moment it is pushed.** Rewriting history afterwards
does not un-publish it — treat anything that was ever pushed as leaked and
rotate it.

### Your Plex token is not a password for this app

It is an admin credential for your Plex server. Anyone holding it can read your
library, your watch history and your account details, and act as you. It is
worth as much as your Plex password and it does not expire on its own.

### Rotating a leaked Plex token

1. Sign in at plex.tv, open **Account → Devices**, and remove the authorised
   devices. Changing your Plex password does the same thing more bluntly and
   signs everything out.
2. Get a fresh token (any library item → **Get Info** → **View XML**, then the
   `X-Plex-Token` in the URL).
3. Put it in `config.yaml` and restart the app.

That rotates *your* token. Household members hold their own, so they are not
signed out by this and do not need to do anything.

### Invalidating everybody's sessions

Household Plex tokens are kept encrypted, because acting on somebody's account
needs the real token rather than a hash of it — a hash cannot sign a request.
The key is `data/secret.key`, created on first run with `0600` permissions.

Delete it and every stored token becomes unreadable at once: nobody stays
signed in, and everybody signs in again with Plex. That is the blunt instrument
if you think the database has been copied off the machine. A new key is
generated on the next start.

If the key itself has leaked *and* somebody has the database, assume every
household token is compromised. Those are their tokens, on their accounts, so
tell them — each of them can sign out their own devices from plex.tv.

---

## What the design already does about the obvious things

Worth knowing so you can report the parts that fall short of it.

**The model cannot be talked into much.** It only ever fills in a fixed set of
filters — genres, a rating floor, a year range — using structured output, so
its reply cannot become free text however the request is phrased. It never
returns film titles. Requests that are plainly not about films are answered
from a fixed list of lines without the model being asked at all, so an
off-topic request costs nothing and there is no generated reply for anybody to
steer. The instruction saying a request is data rather than instruction always
goes last in the prompt, and cannot be moved or removed from configuration.

**Allowances are cost control, not abuse control.** Per person per hour, per
person per day, and a ceiling for the whole server per day. They exist so that
a bored teenager holding down a button cannot run up a bill overnight. Do not
mistake them for a defence against a determined attacker.

**Artwork is proxied narrowly.** The thumbnail route accepts Plex metadata
artwork paths and nothing else, so it cannot be turned into a general reader
for your Plex server using the owner's token.

**Nothing is reachable unless it is named.** The access guard runs before every
request and closes anything it does not recognise, so a new endpoint is private
until someone deliberately opens it.

---

## Scope

**In scope:** anything that lets a stranger reach the household tier, anything
that lets a household member reach the owner tier, anything that leaks a Plex
token or another person's data, and anything that lets a request escape being
data — turning free text into an action, a file read, or a request to a host
you did not configure.

**Out of scope:** the maintenance tooling run deliberately by the owner on
their own machine, which renames and moves files because that is its job;
denial of service by somebody you have already shared your library with; the
absence of a rate limit on an endpoint that costs nothing; and anything that
requires the attacker to already hold your Plex admin token, since at that
point this app is the least of it.

The trust model is worth saying plainly: **the household tier is people you
have already trusted with your entire library**. Plex is the gate, and this app
does not try to be a second one. If you would not share the library with
somebody, do not expect the sign-in to be what stops them.
