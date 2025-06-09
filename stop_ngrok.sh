#!/bin/bash

export PATH=$PATH:/usr/local/bin

# 设置颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查ngrok是否在运行
if ! pgrep -x "ngrok" > /dev/null; then
    echo -e "${YELLOW}ngrok 未运行${NC}"
    exit 0
fi

# 获取ngrok进程ID
NGROK_PID=$(pgrep -x "ngrok")

# 停止ngrok
echo -e "${GREEN}正在停止 ngrok...${NC}"
kill $NGROK_PID

# 等待进程完全停止
sleep 2

# 检查是否成功停止
if pgrep -x "ngrok" > /dev/null; then
    echo -e "${RED}ngrok 未能正常停止，尝试强制终止...${NC}"
    kill -9 $NGROK_PID
    sleep 1
fi

# 最终检查
if ! pgrep -x "ngrok" > /dev/null; then
    echo -e "${GREEN}ngrok 已成功停止${NC}"
    
    # 清理日志文件
    if [ -f "ngrok.log" ]; then
        echo "正在清理日志文件..."
        rm ngrok.log
    fi
else
    echo -e "${RED}无法停止 ngrok，请手动检查进程${NC}"
    exit 1
fi 