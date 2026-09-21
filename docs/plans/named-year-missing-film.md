# Plan: a named film with a year the shelf does not hold

Status: plan only, nothing implemented. Written for a local session to carry
out. Read CONTRIBUTING.md first; it sets the house style and the manual test
paths, and there is no test suite.

## The symptom

Search "Resident evil 2026". The page answers "We have these. 6 things here
match that name." and lists the 2002 film onwards. The 2026 film is never
mentioned, is never offered for the wanted list, and the "include ones we
haven't got" checkbox is ignored. The person wanted the new one and has no
way to ask for it from that screen.

## The root cause

One line, used in three places.

`scanner/wanted_search.py`, `narrow_by_year()` (around line 343):

    exact = [m for m in matches if m["year"] == year]
    return exact or matches

When a year is typed and nothing on the shelf carries that year, the function
hands back every match anyway. The year the person typed is thrown away, and
the callers then treat those matches as certain hits (confidence 2, because
"Resident Evil" is an exact fold of the 2002 title).

The three callers, and what goes wrong in each:

1. **The search.** `api/request_assistant.py` `search_by_title()` (line 910)
   gets six certain matches and claims "We have that". `api/routes.py`
   `request_search()` (line 1326) then returns early at the `if by_title:`
   branch (line 1343) with `captured_missing: []`. Nothing after that line
   runs: no TMDb `did_you_mean`, no `availability()` check, no `close`
   matches, and the `include_missing` checkbox is never read because the
   discovery block sits below the early return.

2. **The ask.** Even if the page offered "Resident Evil (2026)", pressing
   "Ask for it" posts to `/request/wanted` (line 1857), which calls
   `search_and_capture()` (`scanner/wanted_search.py` line 350). Same
   `narrow_by_year`, same six matches, so `found` is true and the page says
   "Good news, we already have that". The row is never inserted.

3. **The recheck.** `recheck()` (`scanner/wanted_search.py` line 403) runs
   `narrow_by_year(search_plex(entry["title"]), entry["year"])` for every
   wanted row. A row for "Resident Evil" year 2026 would be reported as now
   in Plex on the strength of the 2002 file, and `settle_wanted()` in
   `routes.py` (line 583) would mark it acquired and push an "it's arrived"
   notification that is false. This one is silent today only because
   symptom 2 stops the row ever being created.

## The fix

Keep the diff about one thing: a typed year is a constraint, not a hint.

### Step 1. `narrow_by_year` stops falling back

`scanner/wanted_search.py`. When `year` is given and no match carries it,
return the matches with their confidence lowered to 1 rather than returning
them unchanged. Confidence 1 already means "worth offering, never worth
claiming" everywhere (see `title_confidence()` and the comments in
`search_by_title` and `search_and_capture`), so every caller gets the right
behaviour without further changes:

- `search_by_title` finds no certain match and returns `None`.
- `search_and_capture` finds no certain match, inserts the wanted row, and
  the demoted matches surface as `close`, which the page already renders as
  "Nearest thing on the shelf: ... not the one you asked for".
- `recheck` finds nothing playable at confidence 2, so the 2002 file never
  settles a 2026 request.
- `close_matches` in `request_assistant.py` already keeps confidence-1 films,
  so the 2002 film and its sequels show up under "We do have these, if you
  meant one of them".

Plex allows a year to be off by one between metadata agents. Decide whether
`year == m["year"]` should tolerate plus or minus one. Recommendation: no.
A person typing 2026 for a franchise with a 2021 entry does not mean 2021,
and the mismatch cases that matter are all years apart. Say so in the
docstring.

Do not return an empty list. The close matches are the reason the answer
reads as considered rather than blind, and `close_matches` and
`search_and_capture` both need them.

### Step 2. Keep the model out of it

With step 1 alone, `request_search` falls through to the rate-limit check
and then `mood_search`, which calls `translate()` and costs a model call for
what is plainly a title. `translate()` does set `is_title` when the text
contains a digit (line 543), and the model usually does too, so the answer
would come back as `title_missing` and the page would render it correctly.
But it spends the person's allowance, depends on the model being up, and a
missing title with a year is deterministic.

In `search_by_title`, when `year` is set and nothing certain carries it,
return a `title_missing` result directly instead of `None`:

    {
        "request": ..., "kind": "title_missing", "translated_by": "title",
        "filters": {}, "interpretation": "We have not got the <year> one.",
        "playlist_name": "", "matches": [], "shows": [], "unavailable": [],
    }

Only when a year was typed. A bare title that misses should keep going to
`search_by_person` and the mood search as it does now; that path is well
worn and the person's request may not be a title at all.

Then in `request_search`, the `did_you_mean` enrichment (the block under
`if result.get("kind") == "title_missing":`, lines 1433 to 1470) has to run
for this result too. It currently lives below the early return. Lift it into
a nested helper, `explain_missing_title(result, query, token)`, and call it
from both places: the `by_title` branch when `by_title["kind"] ==
"title_missing"`, and where it sits now on the mood path. Keep the body
identical; this is a move, not a rewrite.

`tmdb_lookup.suggest()` already takes `year` and passes it to TMDb as the
`year` parameter, and `parse_query` already extracts it, so
`did_you_mean` will lead with the 2026 film. `availability()` then marks it
`unreleased` or `cinema`, and the page shows "Not out yet, due ..." with the
"Tell me when it lands" button. None of that needs touching.

### Step 3. Honour the checkbox on a title hit

Separate concern, smaller, and optional for this pass. Today a title search
that hits skips the discovery block entirely, so "include ones we haven't
got" does nothing on a title. Since a title result has empty `filters`,
`tmdb_lookup.discover()` has nothing to go on and would refuse anyway. Leave
it, and say in the commit that the checkbox is for mood and genre searches.
If the owner wants it on titles later, the right shape is a franchise
lookup on TMDb (`/movie/{id}/similar` or the collection endpoint), which is
a feature rather than a fix.

## Files touched

- `scanner/wanted_search.py`: `narrow_by_year` (step 1).
- `api/request_assistant.py`: `search_by_title` (step 2).
- `api/routes.py`: `request_search`, lift the enrichment block into a helper
  and call it from the `by_title` branch (step 2).
- `dashboard/request.html`: nothing. The `title_missing` renderer at line
  1717 already draws the guess with its release state, the ask button, and
  the close matches. Check it with the service worker cleared, per
  CONTRIBUTING.

## What to walk by hand

There are no tests. Exercise these against a real Plex and say in the pull
request which were done.

- "Resident evil 2026": expect "We don't have ...", the 2026 film first
  under "Which one do you mean?" with "Not out yet" and "Tell me when it
  lands", and the 2002 to 2021 films under "We do have these". No model call
  and no allowance spent (check `ai_limits` counts before and after).
- "Resident evil" with no year: unchanged, six matches, "We have these".
- "Resident evil 2002": unchanged, one match, "Yes, we have that".
- "Taxi (French)": unchanged; the qualifier path is untouched.
- Press "Tell me when it lands" on the 2026 guess: expect "Watching out
  for Resident Evil (2026)" and a row on the wanted list with year 2026.
- `POST /wanted/recheck` afterwards: the 2026 row must stay wanted.
- A film with a wrong year for something the shelf does hold, say "Inception
  2011": expect the missing card with Inception (2010) under "We do have
  these". That is the honest answer to what was typed; confirm the owner is
  happy with it rather than a fuzzy match.
- Signed out, household, owner: the search path has no tier logic, but the
  ask does, so check the ask as household.

## Out of scope

- Making the year tolerant of plus or minus one (decided against above).
- Discovery on a title hit (step 3).
- Persisting `available_from` on the wanted row. The "Tell me when it lands"
  button is a plain wanted row today; the due date is shown once and not
  stored. Separate change if wanted.
