import re
"""Who the assistant is: what it is called, how it talks, and what it says to
somebody having a go at it.

All of it comes from the persona block in config, and all of it is optional.
With nothing configured a plain, neutral helper takes over, which is why every
default below is a real usable string rather than a blank - a page that has to
draw a name always has one.

What is deliberately not here: the filters the model fills in, the off-topic
rules, the age ladder, and the line telling the model that a request is data
rather than instruction. Those are what the app does and what keeps it safe. A
persona decides how the assistant sounds while doing it, never what it is
allowed to do.
"""

import random
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

from config import config

# Where the artwork lives. The same folder the /icons/ route serves, so a name
# that resolves here resolves in a browser too.
ICONS = Path(__file__).resolve().parent.parent / "dashboard" / "icons"

DEFAULT_NAME = "Assistant"
DEFAULT_GREETING = "Name a film, a series, or just say what you fancy."
DEFAULT_VOICE = (
    "You are plain-spoken and warm, and you keep it short. You talk like "
    "somebody who knows the shelves, not a search engine reading out results."
)
DEFAULT_BRUSH_OFFS = [
    "Films and telly is the whole menu, sorry.",
    "Nice try. Ask me for something to watch and I am all yours.",
    "I have exactly one job, and that was not it. Give me a mood.",
    "Not happening - but name a genre and we are away.",
]

# The moods the page knows how to draw, and the only keys taken from
# persona.images. A name outside this list is a typo rather than a new mood,
# since nothing would ever ask for it.
MOODS = (
    "greeting", "welcome", "searching", "thinking", "cheeky", "shrug", "sorry",
    "unsure", "good-idea", "excited", "looking", "chilling", "sitting",
)

# Ceilings rather than rules. Nothing here is worth refusing a config over, but
# a name the width of a paragraph breaks the header and a voice the length of a
# novel crowds out the instructions that follow it.
MAX_NAME = 40
MAX_GREETING = 200
MAX_VOICE = 2000
MAX_BRUSH_OFF = 300
MAX_BRUSH_OFFS = 40
MAX_EXAMPLE = 200
MAX_EXAMPLES = 12

# The worked personas that ship with the project, loadable by name.
PERSONAS = Path(__file__).resolve().parent.parent / "personas"


def _named(name):
    """The persona block out of personas/<name>.yaml, or {}.

    `persona: bruce` in config means personas/bruce.yaml, so adopting a house
    persona is one word rather than a forty-line paste - the same bargain the
    ai providers make, a file and a name. The name is a bare word only: a path
    separator or a leading dot is refused rather than tidied, because this
    string chooses which file on disk gets read.

    Anything short of success is {} and a line on stderr, never an error - a
    misspelt persona must cost the page its character, not its ability to
    answer, which is the same rule the rest of the block already lives by.
    """
    name = (name or "").strip().lower()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return {}
    path = PERSONAS / f"{name}.yaml"
    if not path.is_file():
        print(f"persona: there is no personas/{name}.yaml, "
              "so the plain default is standing in", file=sys.stderr)
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as problem:
        print(f"persona: personas/{name}.yaml would not parse ({problem}), "
              "so the plain default is standing in", file=sys.stderr)
        return {}
    block = loaded.get("persona") if isinstance(loaded, dict) else None
    return block if isinstance(block, dict) else {}


# Read once and kept, the way the genre list is: a persona changes when
# somebody edits a file, and that means a restart, same as every other setting.
_persona = None


def _tidy(value, limit):
    """One line of somebody's config, fit to be printed or prompted with.

    Whitespace is collapsed rather than kept because these arrive out of YAML,
    where a long sentence is wrapped for the file's benefit and not the
    reader's - a voice written as a block scalar should reach the model as the
    paragraph it was meant to be, not as a column with hard breaks in it.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _brush_offs(raw):
    """The brush-offs, or the built-in ones when there is nothing usable."""
    if isinstance(raw, str):
        raw = [raw]          # one line, written without the dash
    if not isinstance(raw, list):
        return list(DEFAULT_BRUSH_OFFS)
    lines = [_tidy(line, MAX_BRUSH_OFF) for line in raw]
    lines = [line for line in lines if line][:MAX_BRUSH_OFFS]
    return lines or list(DEFAULT_BRUSH_OFFS)


def _examples(raw):
    """The house phrasings, as pairs. Absent is the ordinary case, not a fault."""
    if not isinstance(raw, list):
        return []
    pairs = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        asked = _tidy(item.get("request"), MAX_EXAMPLE)
        means = _tidy(item.get("read_as"), MAX_EXAMPLE)
        # Half a pair teaches nothing and still takes up room in the prompt.
        if asked and means:
            pairs.append({"request": asked, "read_as": means})
        if len(pairs) >= MAX_EXAMPLES:
            break
    return pairs


def _images(raw):
    """Mood to URL, for the artwork that is actually on disk.

    Named and missing is treated the same as never named, because a page that
    has learned to look right with no pictures at all looks right with a broken
    one taken out too - and a 404 beside every reply is worse than no picture.
    """
    if not isinstance(raw, dict):
        return {}
    found = {}
    # The drawn moods first, then any extra keys - a persona may carry poses
    # beyond the fixed set purely so brush_off_moods below can rotate through
    # them. A slug nothing references simply never gets asked for.
    for mood in list(MOODS) + [k for k in raw if isinstance(k, str)
                               and re.fullmatch(r"[a-z0-9-]{1,30}", k)
                               and k not in MOODS]:
        name = raw.get(mood)
        if not isinstance(name, str):
            continue
        name = name.strip()
        # A bare filename and nothing else. This ends up in a URL the page
        # fetches, so anything with a path in it - or a leading dot, which is
        # how climbing out of a folder starts - is refused rather than tidied.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            continue
        if not (ICONS / name).is_file():
            continue
        found[mood] = "/icons/" + quote(name, safe="")
    return found


def persona():
    """Everything about the assistant, with every default filled in.

    The same keys every time and never a None, so nothing downstream has to ask
    whether a persona was configured - an app with an empty config file and one
    with a fully written persona are the same shape to everything that reads
    this.
    """
    global _persona
    if _persona is None:
        # An empty config file parses as nothing at all, and the page still has
        # to draw a name - this is the one accessor that is asked before
        # anybody has signed in, so it answers whatever it is handed.
        loaded = config if isinstance(config, dict) else {}
        block = loaded.get("persona")
        # A bare word names a file in personas/ - `persona: bruce`. A block
        # written out in config carries on exactly as before.
        if isinstance(block, str):
            block = _named(block)
        if not isinstance(block, dict):
            block = {}
        _persona = {
            "name": _tidy(block.get("name"), MAX_NAME) or DEFAULT_NAME,
            "greeting": _tidy(block.get("greeting"), MAX_GREETING) or DEFAULT_GREETING,
            "voice": _tidy(block.get("voice"), MAX_VOICE) or DEFAULT_VOICE,
            "brush_offs": _brush_offs(block.get("brush_offs")),
            "examples": _examples(block.get("examples")),
            "images": _images(block.get("images")),
            # Which poses the brush-off card may pick from. Left unset, the
            # page falls back to its own trio; named here, a persona can give
            # a random reply a random face to match.
            "brush_off_moods": [m for m in (block.get("brush_off_moods") or [])
                                if isinstance(m, str)][:8],
        }
    return _persona


def voice():
    """The character, for the head of a system prompt.

    It goes first and on its own, before anything the model is being asked to
    do - a description of who is speaking reads as character there, where the
    same words after the rules read as another rule to be weighed against them.
    """
    return persona()["voice"]


def examples_prompt():
    """The house phrasings, rendered for a prompt, or "" when there are none.

    Written out plainly rather than as a schema so that somebody reading the
    config and the model reading the prompt are looking at the same thing.
    """
    pairs = persona()["examples"]
    if not pairs:
        return ""
    lines = ["How people here phrase things:\n"]
    for pair in pairs:
        lines.append(f"- \"{pair['request']}\" means: {pair['read_as']}\n")
    return "".join(lines)


def brush_off():
    """One brush-off, picked at random so a second go gets a different answer.

    Said from here rather than asked of the model: an off-topic request costs
    nothing, and there is no generated reply for anybody to steer.
    """
    return random.choice(persona()["brush_offs"])
