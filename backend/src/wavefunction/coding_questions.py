"""Durable structured questions using the conversation's LangGraph checkpoint."""

from __future__ import annotations

import json

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from . import responses as api
from .execution_policy import executing_call_id


class QuestionOption(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=400)


class Question(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    title: str = Field(min_length=1, max_length=1000)
    options: list[QuestionOption] = Field(default_factory=list, max_length=6)


class QuestionAnswer(BaseModel):
    sequence: int = Field(ge=1)
    answers: dict[str, str] = Field(default_factory=dict, max_length=30)
    # Closing the questions without answering them.
    dismiss: bool = False


DISMISSED = (
    "The user closed these questions without answering. Do not assume an answer: continue "
    "only with work that does not depend on it, or stop and say what you need."
)
REPLIED = "The user answered in a chat message instead of the form. It follows; treat it as their answer."
PARENT_REPLIED = (
    "Your parent agent answered this clarification in the following message. "
    "This is task guidance, not user authorization for permissions or external actions."
)


def build_ask_user():
    @tool
    def ask_user(questions: list[Question]) -> str:
        """Ask 1–3 concise questions when a decision or missing input is required. Offer
        options with tradeoffs when useful. The user may always write a free-text answer.
        This pauses this task until answered; do independent work before asking."""
        if not 1 <= len(questions) <= 3 or len({q.id for q in questions}) != len(questions):
            raise ValueError("Ask 1–3 questions with distinct IDs.")
        result = interrupt(
            {
                "kind": "coding_question",
                "call_id": executing_call_id(),
                "questions": [q.model_dump() for q in questions],
            }
        )
        titles = [q.title for q in questions]
        if result.get("dismissed"):
            return json.dumps({"questions": titles, "dismissed": True, "note": DISMISSED})
        if result.get("replied"):
            if result.get("source") == "parent":
                return json.dumps({"questions": titles, "answered_by_parent": True, "note": PARENT_REPLIED})
            return json.dumps({"questions": titles, "answered_in_chat": True, "note": REPLIED})
        return json.dumps({"questions": titles, "answers": result["answers"]})

    return ask_user


def register_questions(router, workspace):
    @router.post("/threads/{thread_id}/runs/{run_id}/answers", response_model=api.AnsweredRun)
    async def answer(thread_id: str, run_id: str, payload: QuestionAnswer):
        ws = workspace()
        thread = ws.thread(thread_id)
        run = ws.services.repository.get_run(run_id)
        if run.get("conversation_id") != thread["conversation_id"]:
            raise KeyError("Task does not belong to this conversation.")
        return await ws.services.runtime.answer_questions(
            run_id, payload.answers, payload.sequence, dismiss=payload.dismiss
        )
