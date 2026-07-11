#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

pip3 install -r pipeline_test/requirements-ecosystem.txt

# 启动A2A服务端（后台运行）
echo "启动A2A服务端..."
python3 examples/a2a/trpc_main.py &
SERVER_PID=$!

# 等待服务端启动
sleep 5

# 运行A2A客户端测试
echo "运行A2A客户端测试..."
python3 examples/a2a/test_a2a.py
# python3 examples/a2a/raw_client.py
# python3 examples/a2a/client.py

# # TeamAgent with Remote A2A Member
# echo "运行 TeamAgent with Remote A2A Member..."
# cd examples/team_member_agent_remote_a2a/
# python3 run_agent.py
# cd -

# 停止服务端
echo "停止A2A服务端..."
kill $SERVER_PID 2>/dev/null || true

# TeamAgent with Claude Member
echo "运行 TeamAgent with Claude Member..."
cd examples/team_member_agent_claude/
python3 run_agent.py
cd -

# python3 examples/ecosystem/langchain_knowledge/custom_document_loader.py
# python3 examples/ecosystem/langchain_knowledge/custom_retriever.py
# python3 examples/ecosystem/langchain_knowledge/custom_text_splitter.py