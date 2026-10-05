# Native V4 agent

See README.zh.md for the project workflow, UPSTREAM.md for attribution and changes,
and docs/participant-guide.en.md for the complete public protocol.

Run agent.py with Python 3.9+. Standard library only. Configure OPENAI_API_KEY,
OPENAI_BASE_URL and OPENAI_MODEL on the platform; no key gives deterministic fallback.
Nightly notice parsing feeds task planning; bounded advice influences the planner.
Use OBSERVER_MODEL_DISABLED=1 for deterministic tests.

Submit the repository root through GitHub after pushing an exact commit:
`python submit_competition.py` from the repository root. See ../README.md.
The single project manifest at ../observer.project.json runs agent/agent.py.
