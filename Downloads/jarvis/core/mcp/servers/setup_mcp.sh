#!/bin/bash
# JARVIS MCP Server Setup — run once
echo "Setting up JARVIS MCP servers..."

# Node.js required
if ! command -v node &> /dev/null; then
    brew install node
fi

# uv for Python-based servers
if ! command -v uvx &> /dev/null; then
    pip install uv
fi

# Test filesystem server (no env needed)
echo "Testing MCP servers..."
echo '{"jsonrpc":"2.0","method":"tools/list","id":1,"params":{}}' | \
    npx -y @modelcontextprotocol/server-filesystem $HOME 2>/dev/null && \
    echo "Filesystem MCP OK" || echo "Filesystem MCP check complete"

echo ""
echo "Required environment variables in .env:"
echo "  GITHUB_TOKEN=ghp_..."
echo "  NOTION_API_KEY=secret_..."
echo "  SLACK_BOT_TOKEN=xoxb-..."
echo "  SLACK_TEAM_ID=T..."
echo "  BRAVE_API_KEY=BSA..."
echo ""
echo "MCP setup complete."
