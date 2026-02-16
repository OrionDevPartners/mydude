import logging

logger = logging.getLogger(__name__)

URGENCY_KEYWORDS = {
    "urgent": ["urgent", "asap", "critical", "emergency", "immediately", "blocking", "blocker", "p0", "sev1"],
    "action": ["todo", "action", "task", "assign", "deadline", "due", "complete", "deliver", "ship"],
    "fyi": ["fyi", "heads up", "note", "update", "informational", "context", "background"],
    "question": ["?", "question", "how do", "what is", "can we", "should we", "why"],
    "decision": ["decided", "decision", "approved", "rejected", "agreed", "consensus", "vote"],
}

def classify_message(text: str) -> dict:
    """Classify a message by urgency and type."""
    lower = text.lower()

    scores = {}
    for category, keywords in URGENCY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[category] = score

    if not scores:
        primary = "fyi"
        confidence = 0.3
    else:
        primary = max(scores, key=scores.get)
        max_score = scores[primary]
        confidence = min(1.0, max_score / 3.0)

    priority_map = {"urgent": "high", "action": "medium", "decision": "medium", "question": "low", "fyi": "low"}

    return {
        "category": primary,
        "priority": priority_map.get(primary, "medium"),
        "confidence": round(confidence, 2),
        "all_categories": scores,
    }

async def ai_triage(text: str, llm) -> dict:
    """Use LLM to classify a message more accurately."""
    try:
        result = await llm.call_team(
            "You are a message triage specialist. Classify the message into exactly one category: URGENT, ACTION, FYI, QUESTION, or DECISION. Also assign a priority: HIGH, MEDIUM, or LOW. Reply in this exact format:\nCATEGORY: <category>\nPRIORITY: <priority>\nSUMMARY: <one line summary>",
            f"MESSAGE:\n{text[:3000]}",
            roles_hint={"openai": "classifier", "anthropic": "prioritizer", "gemini": "summarizer", "grok": "urgency detector"}
        )
        merged = result.get("merged", "")

        category = "fyi"
        priority = "medium"
        summary = text[:100]

        for line in merged.split("\n"):
            line_upper = line.strip().upper()
            if line_upper.startswith("CATEGORY:"):
                cat = line.split(":", 1)[1].strip().lower()
                if cat in ["urgent", "action", "fyi", "question", "decision"]:
                    category = cat
            elif line_upper.startswith("PRIORITY:"):
                pri = line.split(":", 1)[1].strip().lower()
                if pri in ["high", "medium", "low"]:
                    priority = pri
            elif line_upper.startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()

        return {"category": category, "priority": priority, "summary": summary, "ai_analyzed": True}
    except Exception as e:
        basic = classify_message(text)
        basic["ai_analyzed"] = False
        basic["ai_error"] = str(e)[:100]
        return basic
