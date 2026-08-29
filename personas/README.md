# Personas

The assistant that answers requests has no character of its own. It is given
one, in `config.yaml`, under `persona:` — a name, a greeting, a voice, a
handful of lines for people having a go at it, and optionally some artwork.

Two worked examples sit in this folder:

| File | What it is |
| --- | --- |
| `warm-and-chatty.yaml` | Pleased you asked, has opinions, not in a hurry |
| `dry-and-minimal.yaml` | Says the least that will do, and stops |
| `bruce.yaml` | A jet black French bulldog on night watch, made for the Bruce skin |

They exist to show the range rather than to be adopted as they are. Neither is
a default; the default is what `config.example.yaml` ships with.

## Using one

Name it. One word in `config.yaml`, in place of the whole block:

```yaml
persona: bruce
```

That reads `personas/bruce.yaml` — any file in this folder works the same way
— and a restart picks it up. The persona is read once and kept, in the same
way as every other config value, so nothing here changes under a running
process.

To customise one instead, copy it in the old way:

1. Open the file you want, and copy everything from `persona:` downwards.
2. Paste it into `config.yaml`, replacing the `persona:` line or block
   already there, and edit to taste.
3. Restart the server.

Leave a field out and the built-in default takes its place. Leave the whole
block out and a plain, neutral helper answers instead, which is a perfectly
reasonable way to run this.

## The fields

**`name`** — what it calls itself, on the page and in its own instructions.
Forty characters at most. It appears in the greeting line and above anything
it says, so it wants to be short enough to read as a name rather than a title.
This is not the same as `app.name`, which is what the *app* is called; the two
are allowed to differ and usually should.

**`greeting`** — the line under the search box, said the moment somebody
arrives. Two hundred characters at most. Written out in full, because the name
is not stitched into it for you: it is the first thing anybody reads, and a
sentence someone wrote always beats a sentence a template assembled.

**`voice`** — character, handed to the model ahead of every request. Two
thousand characters at most. This is written at the model, not at the person
reading the page: say what the assistant is like and how much of it there is.
Tone is all it controls. What the assistant may answer, the filters it has to
fill in, and the rule that a request is data rather than instruction are fixed
in code and go in after this, so nothing written here can widen the job.

A voice does better for being told how *little* to say as well as how to say
it. Models drift back towards being helpful at length unless brevity is asked
for plainly.

**`brush_offs`** — what it says to somebody who asks it for code, or tries to
talk it into being a general chatbot. One is picked at random, so a second
attempt gets a different answer, and the randomness is the joke. Three
hundred characters each, forty lines at most, though four is plenty.

These are said *instead of* asking the model. An off-topic request therefore
costs nothing, and there is no generated reply for anybody to steer — which is
why writing these is worth the ten minutes rather than leaving the defaults.

**`examples`** — see below. Optional.

**`images`** — see below. Optional.

## Examples, and what they are for

`examples` is how this house talks, written down. Each entry pairs what
somebody actually types with what it should be taken to mean:

```yaml
examples:
  - request: "something my mum would like"
    read_as: "gentle drama, romance or period, nothing violent, rating floor about 7"
```

The pairs go into the prompt ahead of the request, so the model reads the new
request in the light of them. They are worth writing for phrasing a model
could not reasonably guess — household shorthand, a family's idea of "old", a
word that means something particular here and nothing anywhere else. They are
not worth spending on "romcom", which is understood already.

The right-hand side should read as filters, not as a film. The assistant
returns genres, ratings, decades and thresholds, never titles, because a title
can be invented and a filter can only ever select from what is on the shelves.
Write `read_as` in the same terms and the examples teach the thing the model
is actually being asked to do.

Twelve at most. Past that they crowd out the instructions around them, and a
model reading fifteen examples of how to read a request starts paying less
attention to what it was told about the library.

Only the free-text search uses these. The taste suggestions on the home page
do not, because those are about choosing titles from a list, not about reading
filters out of a sentence.

## The artwork

Optional, and genuinely optional: every layout on the page is built to read
properly with no images at all. A mood with no file simply leaves the picture
out, rather than leaving a gap where one should be. One or two is a fine place
to start, and none is a fine place to stay.

Files go in `dashboard/icons`, and are named in config by bare filename:

```yaml
images:
  greeting: "bertie-waving.png"
  welcome:  "bertie-welcome.png"
  cheeky:   "bertie-cheeky.png"
```

A path is refused — only a filename is accepted, and only a file that is
actually on disk is used. Both checks happen once, at startup, so a new
picture needs a restart before it is drawn.

There are thirteen moods, and these spellings are the only ones read:

| Mood | Drawn when | Height |
| --- | --- | --- |
| `greeting` | beside the greeting line, on arrival | 132px |
| `welcome` | the sign-in page, before anybody has signed in | 168px |
| `searching` | while a search runs | 96px |
| `thinking` | once a search is taking its time | 96px |
| `cheeky` | delivering a brush-off | 78px |
| `shrug` | nothing matched | 78px |
| `sorry` | something went wrong | 78px |
| `unsure` | the library might have it, under another name | 78px |
| `good-idea` | a suggestion worth a look | 78px |
| `excited` | something has gone on a list | 78px |
| `looking` | an empty wanted list | 78px |
| `chilling` | no playlists yet | 78px |
| `sitting` | a playlist with nothing in it | 78px |

`greeting` and `welcome` are the two that earn their keep first: they are the
largest, and they are what somebody sees before anything has happened.

### What the files should be

Transparent PNGs. Height is what the page sets — width follows from the
picture, so a wide pose gets a wide picture and nothing is cropped or
squashed.

The tallest use is 168px, and screens are not, so draw for around 400px tall
and let the browser scale down. Anything under about 250px will look soft on a
phone.

Each figure is aligned to the bottom of whatever it stands next to, and drawn
full-body rather than cropped to a head — the pose is the entire point of
having moods, and an avatar throws it away. So keep the feet on the bottom
edge of the canvas with no empty space beneath them, or the character will
appear to float.

A drop shadow is applied by the page, which is another reason the background
has to be properly transparent rather than white.

### Serving and caching

Anything under `/icons/` is served without a sign-in, because the welcome
image is needed before anybody has one, and is cached by the service worker as
an ordinary asset. Replacing a file under a running server therefore wants a
hard refresh on any phone that has already seen the old one. Deleting one is
safe at any time: the page removes a picture that fails to load rather than
showing a broken image.

Artwork you add is yours and not part of this project, so `.gitignore` leaves
`dashboard/icons` alone apart from the handful of icons that ship.

## What is not configurable, and why

The persona is how the assistant sounds. It is not what the assistant is
allowed to do, and the two are kept apart on purpose:

- what counts as an off-topic request
- the filters the model fills in, and what each one means
- the age-rating ladder
- the keyword fallback used when there is no model or no key
- the instruction that a request is data and not an instruction, which is
  always added last and cannot be written around from here

Those are what the app does and what keeps it honest. A persona author can
change every word the assistant says without being able to change any of them,
which is the whole reason a persona is safe to hand to somebody else.
