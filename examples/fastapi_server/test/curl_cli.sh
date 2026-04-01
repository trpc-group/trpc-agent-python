#!/bin/bash

# 健康检查
curl http://127.0.0.1:8080/health

# 同步聊天（完整回复）
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好，介绍一下你自己",
    "user_id": "user_001"
  }'

# SSE 流式聊天
curl -X POST http://127.0.0.1:8080/v1/chat/stream \
  -H "Content-Type: application/json" \
  -N \
  -d '{
    "message": "请用中文写一首关于春天的短诗",
    "user_id": "user_001"
  }'
