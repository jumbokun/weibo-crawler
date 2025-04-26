#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from aiohttp import web
import aiohttp
import os

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 全局变量
WEBSOCKET_CLIENTS = set()
DANMU_CONFIG = {
    "max_comments": 5,  # 最大显示的评论数
    "update_interval": 0.5,  # 更新间隔（秒）
    "container_width": 400,
    "font_size": 16,
    "text_color": "#FFFFFF"
}

# 记录最后推送的评论信息
LAST_COMMENT_TIME = None
LAST_PUSHED_COMMENTS = set()

# 在全局变量部分添加
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）

def load_config():
    """加载弹幕配置"""
    try:
        with open('danmu_config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            DANMU_CONFIG.update(config)
    except Exception as e:
        logger.warning(f"加载配置文件失败: {e}，使用默认配置")

async def get_latest_comments():
    """从数据库获取最新的评论"""
    global LAST_COMMENT_TIME
    try:
        conn = sqlite3.connect('./weibo/weibodata.db')
        cursor = conn.cursor()
        
        # 获取最新的n条评论，按照 id 降序排序确保获取最新的评论
        query = """
        SELECT id, user_screen_name, text, created_at
        FROM comments
        ORDER BY id DESC
        LIMIT ?
        """
        
        cursor.execute(query, (DANMU_CONFIG['max_comments'],))
        comments = cursor.fetchall()
        
        # 转换为列表
        result = []
        for comment in comments:
            result.append({
                'id': comment[0],
                'username': comment[1],
                'message': comment[2],
                'created_at': comment[3]
            })
            
        return result
    except Exception as e:
        logger.error(f"获取评论失败: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def has_new_comments(comments):
    """检查是否有新评论"""
    global LAST_COMMENT_TIME, LAST_PUSHED_COMMENTS
    
    if not comments:
        return False
        
    # 如果是第一次获取评论，认为所有评论都是新的
    if LAST_COMMENT_TIME is None:
        LAST_COMMENT_TIME = comments[0]['created_at']
        LAST_PUSHED_COMMENTS = {comment['id'] for comment in comments}
        return True
    
    # 检查是否有新评论
    current_comment_ids = {comment['id'] for comment in comments}
    
    # 只要有任何新的评论ID就认为有更新
    has_new = bool(current_comment_ids - LAST_PUSHED_COMMENTS)
    if has_new:
        LAST_PUSHED_COMMENTS = current_comment_ids
        
    return has_new

async def broadcast_comments():
    """广播最新评论到所有连接的客户端"""
    first_broadcast = True
    
    while True:
        if WEBSOCKET_CLIENTS:
            comments = await get_latest_comments()
            
            # 检查是否有新评论或是第一次广播
            if first_broadcast or has_new_comments(comments):
                logger.info("检测到新评论，正在推送...")
                # 为每个客户端发送评论
                for ws in WEBSOCKET_CLIENTS.copy():
                    try:
                        # 先清除现有评论
                        await ws.send_json({
                            'type': 'clear',
                            'payload': {}
                        })
                        # 直接按照数据库返回的顺序发送（已经是最新的在前）
                        for comment in comments:
                            await ws.send_json({
                                'type': 'danmu',
                                'payload': {
                                    'username': comment['username'],
                                    'message': comment['message']
                                }
                            })
                    except Exception as e:
                        logger.error(f"发送评论失败: {e}")
                        WEBSOCKET_CLIENTS.remove(ws)
                
                first_broadcast = False
                    
        await asyncio.sleep(DANMU_CONFIG['update_interval'])

async def websocket_handler(request):
    """处理WebSocket连接"""
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
    await ws.prepare(request)
    
    WEBSOCKET_CLIENTS.add(ws)
    logger.info(f"新的WebSocket连接，当前连接数: {len(WEBSOCKET_CLIENTS)}")
    
    # 发送初始样式配置
    await ws.send_json({
        'type': 'style',
        'payload': {
            'containerWidth': DANMU_CONFIG['container_width'],
            'fontSize': DANMU_CONFIG['font_size'],
            'textColor': DANMU_CONFIG['text_color'],
            'lines': DANMU_CONFIG['max_comments']
        }
    })
    
    try:
        # 启动心跳任务
        heartbeat_task = asyncio.create_task(send_heartbeat(ws))
        
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.PONG:
                # 收到PONG响应，说明客户端还活着
                logger.debug("收到客户端心跳响应")
                continue
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f'WebSocket连接错误: {ws.exception()}')
                break
    finally:
        WEBSOCKET_CLIENTS.remove(ws)
        heartbeat_task.cancel()  # 取消心跳任务
        logger.info(f"WebSocket连接关闭，当前连接数: {len(WEBSOCKET_CLIENTS)}")
    
    return ws

async def send_heartbeat(ws):
    """发送心跳包保持连接"""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if ws.closed:
                break
            try:
                await ws.ping()
                logger.debug("发送心跳包")
            except Exception as e:
                logger.error(f"发送心跳包失败: {e}")
                break
    except asyncio.CancelledError:
        pass

async def serve_danmu_html(request):
    """提供弹幕HTML页面"""
    return web.FileResponse('danmu.html')

async def init_app():
    """初始化应用"""
    app = web.Application()
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/danmu.html', serve_danmu_html)
    
    # 启动评论广播任务
    asyncio.create_task(broadcast_comments())
    
    return app

def main():
    """主函数"""
    # 加载配置
    load_config()
    
    # 启动服务器
    app = init_app()
    web.run_app(app, host='localhost', port=8080)

if __name__ == '__main__':
    main() 