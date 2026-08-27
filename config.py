"""Reading config.yaml, and saying something useful when it cannot be read.

All of this happens at import, before a route or a database exists, which is
the point: a missing Plex token is not a fault to report politely on some
later page, it is a server that was never going to work. So the checking is
done once, here, and what comes out the other side is a sentence somebody who
has just cloned this can act on rather than a traceback pointing into a YAML
parser.

Only two values are ever demanded - the address of a Plex server and a token
for it. Everything else is filled in below or is a feature that stays quietly
off until somebody wants it, so a working config.yaml can be three lines long.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_PATH = ROOT / "config.example.yaml"

# Nothing works without these two, so they are worth stopping for. The third
# item is what to say about the one that is missing: the person reading it has
# never seen this codebase and needs telling where the value comes from.
REQUIRED = (
    ("plex", "url", "the address of your Plex server, such as http://localhost:32400"),
    ("plex", "token", "your Plex token - the README says where to find it"),
)

# What the example file leaves in the blanks. Copying it and starting the
# server before filling it in is the usual way to arrive here with a file that
# parses perfectly and still cannot reach a thing, and the failure that comes
# out of Plex for it says nothing helpful at all.
PLACEHOLDERS = ("REPLACE_ME", "CHANGE_ME", "YOUR_")

# Blocks the rest of the app reads without first asking whether they are
# there. Supplied rather than demanded: a config.yaml that says nothing about
# where the database goes is not a mistake, it is somebody who is happy with
# where it goes.
DEFAULTS = {
    "plex": {"url": "", "token": ""},
    # Where the media actually sits, keyed by library. Empty means the
    # scanning side finds nothing, which is a perfectly good way to run this
    # if all you want is the request page.
    "nas": {},
    "app": {},
    "notifications": {},
    # The optional services. Blank keys are how each of them is turned off, so
    # a config.yaml that says nothing about TMDb gets a TMDb that is switched
    # off rather than a server that will not start. Two of the modules behind
    # these read them the moment they are imported, which is early enough that
    # a missing block used to take the whole app down before it had a chance
    # to say why.
    "tmdb": {"api_key": "", "base_url": "https://api.themoviedb.org/3",
             "language": "en-US"},
    "sonarr": {"url": "", "api_key": ""},
    "radarr": {"url": "", "api_key": ""},
    "flask": {"host": "127.0.0.1", "port": 5050, "debug": False},
    "database": {"path": "plex_ops.db"},
}


def _stop(problem, remedy):
    """Say what is wrong and what to do about it, then stop.

    Raised as SystemExit rather than an exception of this module's own so the
    person running it gets the two sentences and nothing else. An exception
    here would be printed with a stack of frames above it, none of which are
    anything to do with the mistake being reported.
    """
    print(f"\n{problem}\n\n{remedy}\n", file=sys.stderr)
    raise SystemExit(1)


def _missing_file_remedy(config_file):
    """How to make the file that is not there."""
    if EXAMPLE_PATH.exists():
        return (f'Copy the example and fill in your own values:\n\n'
                f'    cp "{EXAMPLE_PATH}" "{config_file}"\n\n'
                f"Then set plex.url to the address of your Plex server and "
                f"plex.token to your Plex token. Everything else in there is "
                f"optional and commented.")
    return (f"Create {config_file} with a plex block in it:\n\n"
            f"    plex:\n"
            f'      url: "http://localhost:32400"\n'
            f'      token: "your-plex-token"\n')


def _with_defaults(raw):
    """Fill in the blocks nothing else bothers to check for.

    One level deep only. A block somebody has written out keeps every word of
    what it says; the defaults only supply the keys it left out, so removing a
    line means taking the default rather than taking a crash.
    """
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _check_required(cfg, config_file):
    """Refuse to start on a config that cannot possibly reach Plex."""
    for section, key, what in REQUIRED:
        value = str((cfg.get(section) or {}).get(key) or "").strip()
        if not value:
            _stop(f"{section}.{key} is not set in {config_file}.",
                  f"Open it and put in {what}.")
        if value.startswith(PLACEHOLDERS):
            _stop(f"{section}.{key} in {config_file} is still the value the "
                  f"example file ships with.",
                  f"Replace it with {what}.")


def load_config(path=None):
    config_file = Path(path) if path else CONFIG_PATH

    if not config_file.exists():
        _stop(f"{config_file} is missing.", _missing_file_remedy(config_file))

    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        # The parser's own complaint carries the line and column, which is the
        # only part anybody needs, so it is passed through rather than
        # summarised into something vaguer.
        _stop(f"{config_file} is not valid YAML.",
              f"The parser said:\n\n{e}\n\n"
              f"Indentation is usually the culprit - two spaces per level, "
              f"and no tabs.")
    except OSError as e:
        _stop(f"{config_file} could not be read.", str(e))

    if raw is None:
        _stop(f"{config_file} is empty.", _missing_file_remedy(config_file))
    if not isinstance(raw, dict):
        _stop(f"{config_file} does not read as a set of settings.",
              "It should be blocks of key: value, starting with a plex block. "
              "A list or a bare string at the top level is not one.")

    merged = _with_defaults(raw)
    _check_required(merged, config_file)
    return merged


config = load_config()
