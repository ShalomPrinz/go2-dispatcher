# Future ideas

The list from spec §23.3, kept current. None of these is in v1. Items planned for v2 (verification, the `ASK` status, a stub that simulates motion) are in §23.2 and `docs/architecture.md`.

- **Forward mid-run messages to the LLM.** Forward any message received during a run to the LLM. Decide between letting the model update the plan in place (the dispatcher would need to support replacing a running plan) and limiting it to classifying the message as a stop or replying to the sender with text. In v1, only `stop` is recognised during a task; other messages get `Busy`.
- **Prompt caching** for the static slots (tools, system text, catalog). It reduces cost, but cached tokens are reported separately and cache behaviour differs between skill sets, so token comparisons would need uncached-equivalent reporting. The slot order already puts the static content first.
- **Geofence.** Needs a position source; depends on whether the robot's own position estimate (logged in `state_before` / `state_after`) is usable.
- **Continuous monitoring during a skill.** A watchdog that can abort mid-motion.
- **Further skill granularity tiers** (medium, composite), as separate `skills.dir` folders.
- **Horizon enforcement review.** If the `horizon_rejection` rate is high (even 1 in 100 is a lot), inspect those plans and either switch to truncation or address it another way (for example, stating the horizon more prominently in the prompt).
