# IMPLEMENTATION PLAN: Dedalus Labs MCP Server

This document outlines the step-by-step implementation plan for the Dedalus Labs MCP Server, based on the specifications in `mcp_planning.md` and the structure of the `dedalus-labs/example-dedalus-mcp` repository.

## 1. Project Structure

We will adopt the standard Dedalus MCP project structure:

```text
RelayMCP/
├── .env                  # Environment variables (gitignored)
├── .gitignore
├── pyproject.toml        # Dependency management (uv)
├── uv.lock
├── README.md
├── mcp_planning.md       # (Existing) Project Plans
├── schema.md             # (Existing) Data Schemas
└── src/
    ├── __init__.py
    ├── server.py         # Main entry point & server configuration
    ├── auth.py           # Authentication middleware
    ├── models.py         # Pydantic data models
    └── tools.py          # Tool definitions (check_status, post_status)
```

## 2. Dependencies & Configuration

### `pyproject.toml`
Match the dependencies from the planning doc and example repo.

```toml
[project]
name = "dedalus-mcp-server"
version = "0.1.0"
description = "MCP Server for Dedalus Labs coordination"
requires-python = ">=3.10"
dependencies = [
    "dedalus-mcp",
    "mcp",
    "httpx",
    "pydantic",
    "python-dotenv",
    "uvloop; sys_platform != 'win32'"
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dedalus_mcp_server"]
```

### Environment Variables (`.env`)
```bash
VERCEL_API_URL=https://dedalus-coordination.vercel.app
MCP_PORT=8000
LOG_LEVEL=INFO
GITHUB_TOKEN=... # For testing/local run
```

## 3. Implementation Steps

### Step 1: Data Models (`src/models.py`)
Implement the Pydantic models defined in `mcp_planning.md` and `schema.md`.

```python
from enum import Enum
from typing import List, Optional, Dict, Literal, Any
from pydantic import BaseModel

class OrchestrationAction(str, Enum):
    PULL = "PULL"
    PUSH = "PUSH"
    WAIT = "WAIT"
    SWITCH_TASK = "SWITCH_TASK"
    STOP = "STOP"
    PROCEED = "PROCEED"

class OrchestrationCommand(BaseModel):
    type: Literal["orchestration_command"] = "orchestration_command"
    action: OrchestrationAction
    command: Optional[str] = None
    reason: str
    metadata: Optional[Dict[str, Any]] = None

class LockEntry(BaseModel):
    user: str
    status: Literal["READING", "WRITING"]
    lock_type: Literal["DIRECT", "NEIGHBOR"]
    timestamp: float
    message: Optional[str] = None

class CheckStatusResponse(BaseModel):
    status: Literal["OK", "STALE", "CONFLICT", "OFFLINE"]
    repo_head: str
    locks: Dict[str, LockEntry]
    warnings: List[str]
    orchestration: Optional[OrchestrationCommand] = None

class PostStatusResponse(BaseModel):
    success: bool
    orphaned_dependencies: List[str] = []
    orchestration: Optional[OrchestrationCommand] = None
```

### Step 2: Authentication (`src/auth.py`)
Implement the `AuthenticatedUser` logic using `dedalus_mcp` context.

```python
from dataclasses import dataclass
from typing import Optional
from dedalus_mcp.core import get_context
from mcp.server.fastapi import McpError

@dataclass
class AuthenticatedUser:
    login: str
    name: str = "Unknown"
    email: str = "unknown@example.com"

async def verify_github_token(token: str) -> AuthenticatedUser:
    # TODO: Implement actual GitHub API verification
    # For now, return a dummy user or implement basic validation
    return AuthenticatedUser(login="implemented_user")

async def get_current_user() -> AuthenticatedUser:
    ctx = get_context()
    if not ctx or not ctx.request_context.credentials:
        # Fallback for local testing or raise error
        # In strict production, raise McpError(-32000, "Missing Credentials")
        pass

    token = ctx.request_context.credentials.get("GITHUB_TOKEN") if ctx and ctx.request_context.credentials else None
    if not token:
        # raise McpError(-32000, "Missing GITHUB_TOKEN")
        pass

    # Placeholder: if no token, maybe return a mock user for local dev if safe
    # But per spec, we should validate.
    return await verify_github_token(token or "dummy")
```

### Step 3: Tool Definitions (`src/tools.py`)
Implement `check_status` and `post_status` using `httpx` and the models above.

```python
import os
import httpx
from typing import List, Optional
from dedalus_mcp import tool
from .models import (
    CheckStatusResponse, PostStatusResponse, OrchestrationCommand,
    OrchestrationAction
)
from .auth import get_current_user

VERCEL_URL = os.getenv("VERCEL_API_URL")

@tool()
async def check_status(
    file_paths: List[str], 
    agent_head: str, 
    repo_url: str, 
    branch: str = "main"
) -> CheckStatusResponse:
    """Check status of files before editing. Returns orchestration commands.
    
    Args:
        file_paths: List of file paths (e.g., ["src/auth.ts", "src/db.ts"])
        agent_head: Current git HEAD SHA
        repo_url: Repository URL
        branch: Git branch name
    """
    user = await get_current_user()
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{VERCEL_URL}/api/check_status",
                headers={"x-github-username": user.login},
                json={
                    "file_paths": file_paths, 
                    "agent_head": agent_head,
                    "repo_url": repo_url,
                    "branch": branch
                },
                timeout=5.0
            )
            resp.raise_for_status()
            return CheckStatusResponse(**resp.json())
            
    except (httpx.ConnectError, httpx.TimeoutException):
        return CheckStatusResponse(
            status="OFFLINE",
            repo_head="unknown",
            locks={},
            warnings=["OFFLINE_MODE: Vercel Unreachable"],
            orchestration=OrchestrationCommand(
                action=OrchestrationAction.SWITCH_TASK, 
                reason="System Offline"
            )
        )

@tool()
async def post_status(
    file_paths: List[str], 
    status: str, 
    message: str, 
    agent_head: str, 
    repo_url: str, 
    branch: str = "main", 
    new_repo_head: Optional[str] = None
) -> PostStatusResponse:
    """Update lock status for files. Supports atomic multi-file locking.
    
    Args:
        file_paths: List of file paths
        status: "READING", "WRITING", or "OPEN"
        message: Context message
        agent_head: Current git HEAD SHA
        repo_url: Repository URL
        branch: Git branch name
        new_repo_head: New HEAD SHA (for OPEN)
    """
    user = await get_current_user()
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{VERCEL_URL}/api/post_status",
                headers={"x-github-username": user.login},
                json={
                    "file_paths": file_paths,
                    "status": status,
                    "message": message,
                    "agent_head": agent_head,
                    "new_repo_head": new_repo_head,
                    "repo_url": repo_url,
                    "branch": branch
                },
                timeout=5.0
            )
            
            if resp.status_code == 409:
                return PostStatusResponse(
                    success=False,
                    orchestration=OrchestrationCommand(action=OrchestrationAction.WAIT, reason="Conflict")
                )
                
            resp.raise_for_status()
            return PostStatusResponse(**resp.json())
            
    except Exception:
        return PostStatusResponse(
            success=False,
            orchestration=OrchestrationCommand(action=OrchestrationAction.STOP, reason="Vercel Offline")
        )
```

### Step 4: Server Entry Point (`src/server.py`)
Initialize the `MCPServer` and register the tools.

```python
import os
from dedalus_mcp import MCPServer
from dedalus_mcp.server import TransportSecuritySettings
from dotenv import load_dotenv

# Import tools to register
from .tools import check_status, post_status

load_dotenv()

def create_server() -> MCPServer:
    return MCPServer(
        name="dedalus-mcp-server",
        connections=[], 
        http_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        streamable_http_stateless=True,
    )

async def main():
    server = create_server()
    # Register tools
    server.collect(check_status, post_status)
    port = int(os.getenv("MCP_PORT", 8000))
    await server.serve(port=port)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 4. Verification Plan

1.  **Install Dependencies**:
    ```bash
    uv init
    uv sync
    ```
2.  **Run Server**:
    ```bash
    uv run src/server.py
    ```
3.  **Test with MCP Client**:
    Use an MCP inspector or client to call `check_status` and verify it hits the Vercel API.
