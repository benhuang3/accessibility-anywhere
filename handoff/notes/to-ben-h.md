Ben H,

The risk handoff's integration table lists these files touched by risk-branch:

- `ally/bridge.py`: an `on_page` hook forwards extension page reports to the risk wire.
- `ally/agent.py`: attach the risk wire after proactive startup, stop it on close, and expose risk status.
- `ally/config.py`: validate the risk settings and call caps, plus the trusted-contact keys.
- `ally/default_config.toml`: risk defaults and `trusted_contact`, `contact_topic`, `contact_call_url`.
  These are the `[risk]` keys users set in `config.toml`; `screen_review` defaults to false.

When merging, check that the bridge hook, attach/stop/status lifecycle and config validation all survive,
that the contact keys match the defaults, and that screen review stays opt-in. Run the unit suites and
try the trap and lookalike demos from `docs/RISK_HANDOFF.md`.

Could you add a demo-reset trigger that publishes the bus topic `risk_reset`, such as
`python run.py simulate risk_reset` or a dashboard button? The wire already subscribes; it clears
rehearsal/page memory without replenishing the AI spend budget. This is a request, not a verified CLI command.

Ganesh
