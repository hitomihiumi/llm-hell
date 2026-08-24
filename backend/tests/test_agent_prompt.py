"""The one guardrail against the agent shelling out to reach GitLab or Google.

Nothing enforces this at the tool layer - run_terminal can run anything, by
design, because a coding agent needs a real shell. The only thing standing
between that and `git clone` against a repository this app has no credential
for is what the system prompt says, so it is worth pinning what it says.

Measured against the live stack before this existed: an agent with a working
search_knowledge_base tool still tried `gh repo view` after one search came
back thin, and the shell command failed anyway - it has no GitLab auth of its
own. This is the fix, and these are the phrases that make it one.
"""

from app.services.llm.coding_provider import AGENT_SYSTEM_PROMPT


def test_the_terminal_is_told_it_cannot_reach_the_connected_services():
    assert "no access to GitLab, Google Drive or Gmail" in AGENT_SYSTEM_PROMPT


def test_search_knowledge_base_is_named_as_the_only_way_there():
    assert "search_knowledge_base is the only way to reach them" in AGENT_SYSTEM_PROMPT


def test_the_full_text_tool_is_offered_as_the_alternative_to_a_shell_command():
    assert "read_knowledge_base_result" in AGENT_SYSTEM_PROMPT
    assert "rather than reaching for the terminal" in AGENT_SYSTEM_PROMPT


def test_a_genuine_empty_result_is_reported_rather_than_worked_around():
    assert "Do not fall back to a shell command" in AGENT_SYSTEM_PROMPT
