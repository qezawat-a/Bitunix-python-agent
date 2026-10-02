"""Coding workflows: /bugfixes and /code_review."""
from __future__ import annotations

from .context import AgentContext
from .core import Agent

BUGFIX_DIRECTIVE = """
<workflow name="bugfix">
1. Understand the reported problem and restate it in one line.
2. Locate the relevant code with file_search / file_list / file_read.
3. Reproduce it if you can (terminal_run: run the failing test or command).
4. Make the smallest correct fix with file_edit / file_write.
5. Verify: re-run the test or command and show the output.
6. Report: what was wrong, what you changed (file:line), and the verification
   output. If you could not verify, say exactly why.
</workflow>
"""

REVIEW_DIRECTIVE = """
<workflow name="code-review">
Target: {target}
1. Discover what is there (file_list) and read the important files.
2. Review for: correctness bugs, security issues, error handling, race
   conditions, resource leaks, unclear naming, missing tests, dead code.
3. Report findings grouped as CRITICAL / HIGH / MEDIUM / LOW / NIT, each with
   file:line, what is wrong, why it matters, and a concrete fix.
4. Finish with a short verdict: is this safe to ship, and what must change first.
Be specific and honest. Do not invent problems to look thorough, and do not
pad the report with praise.
</workflow>
"""


async def run_bugfixes(task: str, ctx: AgentContext) -> str:
    if not task.strip():
        return "Describe the bug: /bugfixes <what is broken>."
    agent = Agent(ctx, role="build", directive=BUGFIX_DIRECTIVE)
    return await agent.run(task)


async def run_code_review(target: str, ctx: AgentContext) -> str:
    target = target.strip() or "the whole workspace"
    agent = Agent(ctx, role="build", directive=REVIEW_DIRECTIVE.format(target=target))
    return await agent.run(f"Review {target}.")
