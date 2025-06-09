#!/bin/bash

export PATH=$PATH:/usr/local/bin

# 设置颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 检查ngrok是否在运行
if ! pgrep -x "ngrok" > /dev/null; then
    echo -e "${RED}ngrok 未运行${NC}"
    echo "使用 'start_ngrok.sh' 启动服务"
    exit 1
fi

# 获取ngrok状态
echo -e "${BLUE}正在获取 ngrok 状态...${NC}"
echo "----------------------------------------"

# 获取HTTP隧道信息
echo -e "${GREEN}HTTP 隧道状态:${NC}"
curl -s http://localhost:4040/api/tunnels | jq -r '.tunnels[] | select(.proto=="https") | "URL: \(.public_url)"'

# 获取TCP隧道信息
echo -e "\n${GREEN}TCP 隧道状态:${NC}"
curl -s http://localhost:4040/api/tunnels | jq -r '.tunnels[] | select(.proto=="tcp") | "地址: \(.public_url)"'

# 显示连接统计
echo -e "\n${GREEN}连接统计:${NC}"
curl -s http://localhost:4040/api/tunnels | jq -r '.tunnels[] | "隧道: \(.name)\n  状态: \(.status)\n  转发: \(.config.addr)"'

# 显示最近的日志
echo -e "\n${GREEN}最近的日志:${NC}"
tail -n 5 ngrok.log

echo "----------------------------------------"
echo -e "${YELLOW}提示:${NC}"
echo "1. 使用这些地址配置您的直播软件"
echo "2. 如果地址不可用，请检查 ngrok.log"
echo "3. 使用 'stop_ngrok.sh' 停止服务" 