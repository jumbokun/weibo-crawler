#!/bin/bash

export PATH=$PATH:/usr/local/bin

# 设置颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查ngrok是否已经在运行
if pgrep -x "ngrok" > /dev/null; then
    echo -e "${YELLOW}ngrok 已经在运行中${NC}"
    echo "使用 'show_ngrok.sh' 查看状态"
    exit 1
fi

# 启动ngrok
echo -e "${GREEN}正在启动 ngrok...${NC}"
nohup /usr/local/bin/ngrok start --config=ngrok.yml --all > ngrok.log 2>&1 &

# 等待ngrok启动
sleep 3

# 检查ngrok是否成功启动
if pgrep -x "ngrok" > /dev/null; then
    echo -e "${GREEN}ngrok 已成功启动${NC}"
    echo "日志文件: ngrok.log"
    echo "使用 'show_ngrok.sh' 查看状态"
    echo "使用 'stop_ngrok.sh' 停止服务"
else
    echo -e "${RED}ngrok 启动失败${NC}"
    echo "请检查 ngrok.log 文件查看错误信息"
    exit 1
fi

# 显示初始状态
./show_ngrok.sh 