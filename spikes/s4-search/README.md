# S4: Search and lyrics

Runbook for spike S4 in [docs/PLAN.md](../../docs/PLAN.md) §6. It runs on any machine with internet access, from a terminal in the repo folder.

**What it answers:** when someone types a song the way they would on the TV (§5.1), does YouTube Music's "songs" search put the right recording in its first 3 results, how does a plain `yt-dlp` YouTube search compare as the fallback, and how many of the songs have lyrics on LRCLIB, synced or plain?

**Pass criterion:** the right version is in the ytmusic top 3 for at least 18 of the 20 test songs. A live recording counts as right when it is the version people know: in some genres the live recording is the best-known one. LRCLIB coverage is measured, not pass or fail.

## Files

| File | What it is |
|---|---|
| `search.py` | Runs the three searches for every song in the list and writes the results. Its dependencies (`ytmusicapi`, `yt-dlp`) are declared inline, so `uv run` installs them in a throwaway environment. |
| `test_search.py` | Unit tests for the song list parsing, the LRCLIB classification and route choice, and the markdown table. No network. |

Run the tests from the repo root with `python -m unittest discover -s spikes/s4-search -v`. They use the standard library only, because every client is passed in as a fake.

## How the search works

Per song, one at a time, with a 0.3 s pause between songs (LRCLIB asks batch clients for 200 to 500 ms):

1. **ytmusic.** `YTMusic().search(query, filter="songs")`, keeping the first 3 results: video id, title, artists, album and duration. The query is the title and the artist as typed, joined by a space. `search` returns about 20 results whatever `limit` says, so the script slices them.
2. **yt-dlp.** `ytsearch3:<query>` with flat extraction: it lists the hits (id, title, channel, duration) without resolving any video, so it needs no JS runtime and downloads nothing.
3. **LRCLIB**, for the ytmusic #1 result only:
   - `GET /api/get` with `track_name`, `artist_name` (all artists joined with ", "), `album_name` and `duration`. LRCLIB matches the duration within 2 s, and a different album name also makes it answer 404.
   - On 404, `GET /api/search` with `track_name` and `artist_name`, taking the first hit whose duration is within 2 s of the #1 duration.
   - The result is classified as `synced` (has line-timed lyrics), `plain` (text only), `instrumental` (LRCLIB marks the track as having no lyrics) or `none`, with the route that matched (`get` or `search`). When ytmusic returned nothing, LRCLIB is `not checked`.

An error on one source of one song (network, rate limit, an unexpected reply) is recorded for that song as `error: …`, and the run goes on. Requests to LRCLIB carry a `User-Agent` naming the spike and this repository, as its [API docs](https://lrclib.net/docs) ask.

## What you need

- [uv](https://docs.astral.sh/uv/) (it fetches a Python 3.12 or later if none is installed).
- The test song list in `spikes/songs.local.txt` (gitignored): about 20 songs that reflect what people will actually sing (§6 prerequisites).

## 1. Write the song list

One song per line, as `Title - Artist`, typed the way someone would type it on the TV. Typos and missing accents stay: they are part of the test. Lines starting with `#` and blank lines are skipped. The line is split on the last ` - `, so a title may itself contain ` - ` or parentheses.

```
# placeholder example
Song Title - Artist Name
Song Title - Part Two (Live) - Artist Name
```

## 2. Run

```
uv run spikes/s4-search/search.py
```

Options: `--songs PATH` (default `spikes/songs.local.txt`) and `--out DIR` (default `spikes/s4-search/`). It prints one line per song and a summary at the end: the number of songs, the LRCLIB count per class, and the median time per source. 20 songs take about a minute.

It writes two gitignored files into `--out`:

- `results.local.json`: everything, including every result, the LRCLIB record that matched (id, names, duration and line counts, not the lyrics text), each query time, every error, and the `ytmusicapi` and `yt-dlp` versions.
- `results.local.md`: one row per song, with the line as typed, ytmusic #1, #2 and #3 as `title · artists · album · m:ss`, yt-dlp #1 as `title · channel · m:ss`, the LRCLIB status and an empty **Verdict** column.

## 3. Fill the verdict column

For each row of `results.local.md`, write which ytmusic result is the version you meant: `1`, `2` or `3`, or `none` if none of the three is. Judge the recording, not the metadata: a live version that is the one people know counts, a cover, a remix, a karaoke track or a different recording with the same title does not. When you are not sure from the title and album, open `https://music.youtube.com/watch?v=<videoId>` with the id from the JSON.

Also note, for the `none` rows, whether the yt-dlp #1 is the right version, and, for a few `synced` rows, whether the lyrics belong to the right song (open `https://lrclib.net/api/get/<id>` with the record id from the JSON).

## Recording the results

The song list is personal, so the filled table stays out of git. Write the summary below at the end of `spikes/s4-search/results.local.md`. Only a generic summary, with counts and no song names, goes into `docs/PLAN.md`.

```markdown
## Summary: <date>

ytmusicapi / yt-dlp versions:
Songs:

## Search
| Verdict | Songs |
|---|---|
| 1 | |
| 2 | |
| 3 | |
| none | |

Right version in the top 3 (target 18 of 20):
Of the `none` rows, how many had the right version as yt-dlp #1:
What the misses have in common (typos, live versions, duets, covers ranking first):

## Lyrics (LRCLIB)
| Status | Songs | Via get | Via search |
|---|---|---|---|
| synced | | | |
| plain | | | |
| instrumental | | | |
| none | | | |

Synced lyrics checked by hand, and whether they matched the song:

## Times (median per song, from the summary)
ytmusic: s · yt-dlp: s · LRCLIB: s

## Verdict
Pass / fail, and why:
```
