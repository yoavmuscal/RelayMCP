import asyncio

from dedalus_mcp import MCPServer
from dotenv import load_dotenv

from .tools import check_status, post_status

load_dotenv()

server = MCPServer("relay-mcp")

server.collect(check_status, post_status)

if __name__ == "__main__":
    asyncio.run(server.serve())
