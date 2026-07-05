import os
import re
import json
import logging
import sys
from typing import Any
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.tools import AgentTool, McpToolset
from google.adk.workflow import Workflow, START, node
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.genai import types
from mcp import StdioServerParameters

from app.config import config

# Set up audit logger
logging.basicConfig(level=logging.INFO)
audit_logger = logging.getLogger("audit_log")

# Define StdioParameters for the local MCP server
mcp_toolset = McpToolset(
    connection_params=StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
    )
)

# Specialist sub-agents
grocery_agent = LlmAgent(
    name="grocery_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are a grocery assistant. Use your tools to add items to the grocery list, "
        "retrieve the list, or clear the list. Always respond with the result of the action."
    ),
    tools=[mcp_toolset],
)

# Chore & Maintenance agent
chore_agent = LlmAgent(
    name="chore_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are a chore and home maintenance scheduler. Use your tools to schedule "
        "new chores/maintenance tasks or list existing chores. Always respond with the result of the action."
    ),
    tools=[mcp_toolset],
)

# Structured Output Schemas
class OrchestratorResponse(BaseModel):
    response: str = Field(description="The final message or result of the action to display to the user.")
    needs_confirmation: bool = Field(description="True if the user is asking to clear the grocery list or schedule high-priority/sensitive chores (e.g. HVAC, electrical, plumbing).")
    confirmation_message: str = Field(default="", description="The question to ask the user to confirm the action (e.g., 'Are you sure you want to clear your grocery list?').")

# Orchestrator agent
orchestrator = LlmAgent(
    name="orchestrator",
    model=Gemini(model=config.model),
    instruction=(
        "You are the main coordinator for the Life Organizer agent. "
        "Your goal is to delegate the user's request to the appropriate specialist agent: "
        "- Use the `grocery_agent` tool to manage grocery lists (add, show, clear items). "
        "- Use the `chore_agent` tool to manage chores and home maintenance tasks (schedule or show chores). "
        "After getting the result from the specialist agent, return your response in the structured output format. "
        "If the user is asking to clear the grocery list or schedule a high-priority/sensitive home maintenance chore "
        "(e.g., HVAC filters, electrical, plumbing), set `needs_confirmation` to true and provide a clear confirmation message."
    ),
    tools=[AgentTool(grocery_agent), AgentTool(chore_agent)],
    output_schema=OrchestratorResponse,
)

# Helper function to extract text safely from types.Content or raw input
def extract_text(node_input: Any) -> str:
    if hasattr(node_input, "parts") and node_input.parts:
        return "".join(part.text for part in node_input.parts if hasattr(part, "text") and part.text)
    elif isinstance(node_input, str):
        return node_input
    return str(node_input)

# Workflow node: Security Checkpoint
def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    user_input = extract_text(node_input)
    scrubbed_input = user_input

    # 1. PII scrubbing: look for emails and phone numbers
    email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    if re.search(email_pattern, scrubbed_input):
        scrubbed_input = re.sub(email_pattern, "[REDACTED_EMAIL]", scrubbed_input)
        audit_logger.warning(json.dumps({
            "event": "pii_redacted",
            "type": "email",
            "session_id": ctx.session.id,
            "severity": "WARNING"
        }))

    phone_pattern = r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"
    if re.search(phone_pattern, scrubbed_input):
        scrubbed_input = re.sub(phone_pattern, "[REDACTED_PHONE]", scrubbed_input)
        audit_logger.warning(json.dumps({
            "event": "pii_redacted",
            "type": "phone",
            "session_id": ctx.session.id,
            "severity": "WARNING"
        }))

    # 2. Prompt injection detection
    injection_keywords = ["ignore previous instructions", "system prompt", "override", "you are now a"]
    detected_injection = False
    for kw in injection_keywords:
        if kw in user_input.lower():
            detected_injection = True
            break

    if detected_injection:
        audit_logger.error(json.dumps({
            "event": "security_violation",
            "type": "prompt_injection",
            "session_id": ctx.session.id,
            "severity": "CRITICAL",
            "raw_input": user_input
        }))
        return Event(
            route="denied",
            state={"security_error": "Input blocked: Prompt injection detected."}
        )

    # 3. Domain-specific safety check (unsafe tasks)
    unsafe_keywords = ["bomb", "explode", "kill", "suicide", "murder", "weapon"]
    detected_unsafe = False
    for kw in unsafe_keywords:
        if kw in user_input.lower():
            detected_unsafe = True
            break

    if detected_unsafe:
        audit_logger.error(json.dumps({
            "event": "security_violation",
            "type": "unsafe_task",
            "session_id": ctx.session.id,
            "severity": "CRITICAL",
            "raw_input": user_input
        }))
        return Event(
            route="denied",
            state={"security_error": "Input blocked: Unsafe task description."}
        )

    # Safe input approved
    audit_logger.info(json.dumps({
        "event": "input_approved",
        "session_id": ctx.session.id,
        "severity": "INFO"
    }))

    return Event(
        output=scrubbed_input,
        route="approved",
        state={"sanitized_input": scrubbed_input}
    )

# Workflow node: Security Block Handler
def security_block(ctx: Context, node_input: Any) -> Event:
    err = ctx.state.get("security_error", "Security violation detected.")
    return Event(state={"final_response": f"⚠️ Security Alert: {err}"})

# Workflow node: Router logic to check if HITL is required
def router_node(ctx: Context, node_input: Any) -> Event:
    # node_input is the dictionary output from orchestrator
    if not isinstance(node_input, dict):
        node_input = {}
        
    response = node_input.get("response", "")
    needs_confirm = node_input.get("needs_confirmation", False)
    confirm_msg = node_input.get("confirmation_message", "")

    if needs_confirm:
        return Event(
            route="confirm",
            state={
                "pending_response": response,
                "confirmation_message": confirm_msg
            }
        )
    else:
        return Event(
            route="complete",
            state={
                "final_response": response
            }
        )

# Workflow node: Human-in-the-Loop Node
async def hitl_node(ctx: Context, node_input: Any):
    if not ctx.resume_inputs:
        msg = ctx.state.get("confirmation_message", "Are you sure you want to perform this action?")
        yield RequestInput(interrupt_id="user_confirm", message=msg)
        return

    # User response received
    confirm_response = ctx.resume_inputs.get("user_confirm", "")
    if any(keyword in confirm_response.lower() for keyword in ["yes", "approve", "confirm", "y"]):
        pending_resp = ctx.state.get("pending_response", "")
        yield Event(
            output=f"Action confirmed and executed. {pending_resp}",
            state={"final_response": f"Action confirmed and executed. {pending_resp}"}
        )
    else:
        yield Event(
            output="Action cancelled by the user.",
            state={"final_response": "Action cancelled by the user."}
        )

# Workflow node: Final Output Generator
def final_output(ctx: Context, node_input: Any):
    final_resp = ctx.state.get("final_response", "No response generated.")
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=final_resp)]))
    yield Event(output=final_resp)

# Constructing Workflow Graph
workflow_agent = Workflow(
    name="life_organizer_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"approved": orchestrator, "denied": security_block}),
        (orchestrator, router_node),
        (router_node, {"confirm": hitl_node, "complete": final_output}),
        (hitl_node, final_output),
        (security_block, final_output),
    ]
)

# Export App
app = App(
    root_agent=workflow_agent,
    name="app",
    resumability_config=ResumabilityConfig(enabled=True),
)
