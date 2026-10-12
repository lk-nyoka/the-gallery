"""The Gallery's newsroom: agents that propose, code that verifies, a producer who decides.

Agents (Microsoft Agent Framework workflow executors):
  Editor-in-Chief  chooses which detected stories to commission, and why
  Analyst          gathers evidence through read-only tools (never raw data)
  Writer           drafts a story as claim-carrying sentences, then revises from verifier feedback
  Verifier (tool)  deterministic code; the only component that can set a verdict
  Desk             checks coverage of every goal and red card; commissions what's missing
  Corrections      when the data changes, drafts replacements for affected approved stories
  Localiser        adapts approved stories to each market's angle and language
"""
