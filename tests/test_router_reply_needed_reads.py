""""Any emails I need to reply to?" is a question, so it routes as a read.

Before rc1 it reached the email read+write route, which offers send_email and
reply_to_email for a question that asked for nothing to be sent. These
phrasings now join "Which emails should I reply to?" on the source-grounded
attention read (collect_task_evidence only). Anything that adds a filter the
read cannot honor (a sender) or an action (draft, reply) keeps its old owner.
Routing only; nothing here executes a tool.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_scratch = tempfile.TemporaryDirectory()
os.environ["HARBOR_HOME"] = _scratch.name

import pytest  # noqa: E402

import service.tools  # noqa: E402,F401
from service.router.router import route  # noqa: E402
from service.workflows.workflow20 import attention_read  # noqa: E402

OUTBOUND = {"send_email", "send_message", "reply_to_email", "forward_email",
            "draft_email", "draft_message"}

READS = {
    "Any emails I need to reply to?": ("email",),
    "any emails i need to respond to": ("email",),
    "Do I have any emails I should reply to?": ("email",),
    "Are there any emails or texts I should respond to?": ("email", "messages"),
    "Any texts I need to respond to?": ("messages",),
    "Do I have any messages that need a reply?": ("messages",),
    "any mail that needs a response": ("email",),
    "Any emails waiting for my reply?": ("email",),
    "Are there any emails still waiting on a response?": ("email",),
    "Do I have any texts I have to get back to?": ("messages",),
    "Anything I need to reply to?": ("email", "messages"),
    "Is there anything I should respond to?": ("email", "messages"),
    # Already covered before rc1; must keep working.
    "Which emails should I reply to first?": ("email",),
    "What should I reply to first?": ("email", "messages"),
}


@pytest.mark.parametrize("prompt,sources", sorted(READS.items()))
def test_reply_needed_question_is_a_read(prompt, sources):
    read = attention_read(prompt)
    assert read is not None and read.sources == sources
    decision = asyncio.run(route(prompt))
    assert decision.reason == "source-grounded personal priorities"
    assert set(decision.tool_subset) == {"collect_task_evidence"}
    assert not (set(decision.tool_subset) & OUTBOUND)
    assert [name for name, _ in decision.direct_calls] == ["collect_task_evidence"]


@pytest.mark.parametrize("prompt", [
    "Any emails from Sam I need to reply to?",            # sender filter the read can't honor
    "Any emails I need to reply to? Draft replies for them.",
    "Reply to any emails that need a reply",
    "Any calendar events I need to reply to?",             # not a communication source
])
def test_actions_and_filters_keep_their_owner(prompt):
    assert attention_read(prompt) is None


# App audit 1.3.2: importance is the intent, not a word the source must
# contain, and Notes and Reminders can hold the important item too.
from service.workflows.workflow20 import priority_read  # noqa: E402

IMPORTANT = {
    "any important notes today?": ("notes",),
    "are there any important reminders today?": ("reminders",),
    "any important emails?": ("email",),
    "what important messages did I get?": ("messages",),
    "which notes are important?": ("notes",),
    "any urgent reminders for tomorrow?": ("reminders",),
    "do I have any important notes or reminders this week?": ("notes", "reminders"),
}


@pytest.mark.parametrize("prompt,sources", sorted(IMPORTANT.items()))
def test_important_source_questions_read_that_source(prompt, sources):
    read = attention_read(prompt)
    assert read is not None and read.sources == sources
    args = read.bindings["collect_task_evidence"]
    assert {name for name in ("email", "messages", "calendar", "notes", "reminders")
            if args["include_" + name]} == set(sources)
    # An overdue or undated reminder can be the important one.
    assert args["include_overdue"] is args["include_undated"] is ("reminders" in sources)
    decision = asyncio.run(route(prompt))
    assert decision.reason == "source-grounded personal priorities"
    assert set(decision.tool_subset) == {"collect_task_evidence"}
    assert "web_search" in decision.forbidden_tools


def test_an_important_question_keeps_its_named_day():
    assert attention_read("any urgent reminders for tomorrow?").bindings[
        "collect_task_evidence"]["period"] == "tomorrow"


@pytest.mark.parametrize("prompt", ["what's important today?", "anything important for me today?",
                                    "is there anything urgent?", "what's important tomorrow?"])
def test_whats_important_reads_every_source(prompt):
    read = priority_read(prompt)
    assert read is not None and set(read.sources) == {"email", "messages", "calendar", "notes", "reminders"}
    decision = asyncio.run(route(prompt))
    assert set(decision.tool_subset) == {"collect_task_evidence"}
    assert "web_search" in decision.forbidden_tools


@pytest.mark.parametrize("prompt", ["any important emails from Sam?", "what's important in tech today?",
                                    "any important news today?", "any important notes? Draft a summary."])
def test_filters_topics_and_actions_keep_their_owner(prompt):
    assert attention_read(prompt) is None and priority_read(prompt) is None


@pytest.mark.parametrize("prompt", ["any important news today?", "important news today"])
def test_important_news_stays_a_web_search(prompt):
    decision = asyncio.run(route(prompt))
    assert "web_search" in (decision.tool_subset or ())
