# Card-Pusher

Push, don't pull. An agent that has to search its own knowledge before it answers often does not. Card-Pusher is a Hermes plugin that does the search for it: before every model call it looks up the cards that match the user's message and puts the best ones into the prompt.

Version 1.1.1 · Apache-2.0

## How it works

- It registers one hook, `pre_llm_call`. There is no proxy, no service and no port.
- It takes the user's message, drops stop words and runs a keyword search with QMD, a local Markdown search command line tool (`qmd search`). If all terms together find nothing, it searches each specific term alone and merges the results. If that finds nothing too, it falls back to a vector search (`qmd vsearch`).
- It returns the best hits as context, labelled as quotation from the knowledge store. Instructions inside a card are not to be followed.
- It is fail-silent: a missing `qmd`, an error or a timeout never blocks the model call. The call goes on without cards.
- Every hook call writes one line to `$HERMES_HOME/logs/cardpusher.jsonl` (query hash, search terms, mode, hits, whether anything was injected, milliseconds, error).
- The same message in the same session is not pushed twice.

## Install

1. Install QMD and index your cards into a collection (default name `wiki`).
2. Copy this folder to `$HERMES_HOME/plugins/card-pusher` and add `card-pusher` to `plugins.enabled` in the Hermes `config.yaml`.
3. Restart Hermes.

Check the parsers and the fallback chain without network or model: `python __init__.py` prints `selbsttest ok`.

## Configuration

All settings are environment variables and optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CARDPUSHER_QMD` | found on `PATH`, `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.bun/bin` | path to the `qmd` executable |
| `CARDPUSHER_COLLECTIONS` | `wiki` | comma list of QMD collections; `*` searches all |
| `CARDPUSHER_MAX_HITS` | `5` | cards per turn |
| `CARDPUSHER_TIMEOUT` | `4` | seconds for a keyword search call |
| `CARDPUSHER_VTIMEOUT` | `10` | seconds for the vector fallback (the first call loads the embedding model) |
| `CARDPUSHER_SNIPPET` | `500` | maximum characters per card |

On expiry of a time limit the whole `qmd` process group is killed.

## Notes

- The injected header and the stop-word list are partly German; the search itself works on any language QMD indexes.
- Earlier versions of this repository (commit `76c6e62`) were an OpenAI-format reverse proxy with a separate retrieval adapter. That design was replaced by the plugin; the proxy is no longer maintained and is not part of this version.

## Licence

Apache-2.0, see [LICENSE](LICENSE).
