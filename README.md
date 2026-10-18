# Card-Pusher (memory proxy)

Push, don't pull. Instead of hoping an agent searches its knowledge, this reverse-proxy sits in the model path and injects the top relevant cards into the system prompt before every model call.

- OpenAI-format reverse proxy (chat/completions): point your agent's base_url at it.
- Fail-silent: if retrieval is slow or down, requests pass through without injection - the agent never stalls on memory.
- Forwards the Authorization header unchanged (works with bearer-token providers).
- Stdlib-only, localhost by default.

Parts:
- proxy.py (:8787) - the reverse proxy. Config via env MEMORY_PROXY_UPSTREAM / MEMORY_PROXY_ABRUF / MEMORY_PROXY_K.
- abruf.py (:8791) - an example retrieval adapter returning the top cards for a query. Swap it for any endpoint that returns cards (e.g. a Reraise card store / MCP server).

Rationale: agents query an external knowledge layer on their own only rarely, so the relevant cards are pushed in rather than pulled.

License: Apache-2.0
