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
import subprocess
import signal
import re
import requests

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

# 记录所有爬虫进程
CRAWLER_PROCS = []
# 记录正在监控的微博ID或BID及其进程
MONITORING_IDS = {}

def get_weibo_id_by_bid(bid, cookie):
    """通过BID获取微博ID"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36",
        "Cookie": cookie
    }
    
    try:
        # 使用新的API直接获取微博信息
        url = f"https://weibo.com/ajax/statuses/show?id={bid}"
        logger.info(f"正在请求URL: {url}")
        logger.info(f"Headers: {headers}")
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # 先看看原始响应
        raw_text = response.text
        logger.info(f"API响应内容: {raw_text[:200]}...")  # 只打印前200个字符避免日志过长
        
        # 检查响应状态码
        logger.info(f"响应状态码: {response.status_code}")
        logger.info(f"响应头: {dict(response.headers)}")
        
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败，响应内容: {raw_text}")
            raise
        
        if 'id' in data:
            logger.info(f"成功将BID {bid} 转换为微博ID {data['id']}")
            return str(data['id'])
        elif 'ok' in data and data.get('ok') == 1 and 'idstr' in data:
            # 处理特殊情况：某些API返回格式不同
            logger.info(f"成功将BID {bid} 转换为微博ID {data['idstr']}")
            return str(data['idstr'])
        else:
            logger.error(f"未能从API响应中获取到微博ID: {data}")
            return None
            
    except Exception as e:
        logger.error(f"转换微博ID失败: {str(e)}")
        logger.error(f"错误类型: {type(e)}")
        if isinstance(e, requests.exceptions.RequestException):
            logger.error(f"请求异常详情: {str(e)}")
        return None

def is_weibo_id(id_str):
    """判断是否为微博ID（数字）"""
    return id_str.isdigit()

def is_bid(id_str):
    """判断是否为BID（字母数字组合）"""
    return bool(re.match(r'^[A-Za-z0-9]+$', id_str))

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
        # 检查comments表是否存在，不存在则创建
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id varchar(20) NOT NULL,
                weibo_id varchar(32) NOT NULL,
                user_screen_name varchar(64) NOT NULL,
                text varchar(1000),
                created_at varchar(20),
                PRIMARY KEY (id)
            );
        """)
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

async def serve_index_html(request):
    """提供主页面"""
    return web.FileResponse('index.html')

async def serve_danmu_html(request):
    return web.FileResponse('danmu.html')

def extract_id_from_input(input_str):
    """从输入中提取ID，支持完整链接、BID和微博ID"""
    # 如果包含/，取最后一个部分
    if '/' in input_str:
        input_str = input_str.split('/')[-1]
    
    # 去除可能的查询参数
    if '?' in input_str:
        input_str = input_str.split('?')[0]
        
    return input_str.strip()

async def start_crawler(request):
    """根据输入的ID启动爬虫脚本，并在8小时后自动关闭"""
    try:
        data = await request.json()
        raw_input = data.get('id', '').strip()
        if not raw_input:
            return web.json_response({'msg': 'ID不能为空'}, status=400)
            
        # 从输入中提取ID
        input_id = extract_id_from_input(raw_input)
        
        # 读取cookie
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                cookie = config.get('cookie', '')
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return web.json_response({'msg': '读取配置文件失败'}, status=500)
            
        # 判断ID类型并获取微博ID
        if is_weibo_id(input_id):
            weibo_id = input_id
            id_type = '微博ID'
            monitor_key = weibo_id
        elif is_bid(input_id):
            weibo_id = get_weibo_id_by_bid(input_id, cookie)
            if not weibo_id:
                return web.json_response({'msg': '无法获取微博ID，请检查BID是否正确'}, status=400)
            id_type = 'BID'
            monitor_key = input_id
        else:
            return web.json_response({'msg': '无效的ID格式'}, status=400)
            
        # 判断是否已在监控
        proc = MONITORING_IDS.get(monitor_key)
        if proc and proc.poll() is None:
            return web.json_response({'msg': f'已经在监控该{id_type}，无需重复启动'} , status=200)

        # 启动爬虫脚本（后台运行，不阻塞主进程）
        proc = subprocess.Popen(['python', 'get_single_weibo_comments.py', weibo_id])
        CRAWLER_PROCS.append(proc)
        MONITORING_IDS[monitor_key] = proc
        logger.info(f"已启动爬虫进程，PID={proc.pid}，{id_type}={input_id}，微博ID={weibo_id}")
        
        # 启动定时任务，8小时后终止该进程
        async def kill_proc_later(pid, delay_sec):
            await asyncio.sleep(delay_sec)
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"已自动终止爬虫进程 PID={pid}")
            except Exception as e:
                logger.error(f"自动终止爬虫进程失败: {e}")
                
        asyncio.create_task(kill_proc_later(proc.pid, 8*3600))
        return web.json_response({'msg': f'已启动爬虫，{id_type}={input_id}，微博ID={weibo_id}，8小时后自动关闭'})
    except Exception as e:
        logger.error(f"启动爬虫失败: {e}")
        return web.json_response({'msg': f'启动失败: {e}'}, status=500)

async def init_app():
    """初始化应用"""
    app = web.Application()
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/', serve_index_html)
    app.router.add_get('/danmu.html', serve_danmu_html)
    app.router.add_post('/api/start_crawler', start_crawler)
    
    # 启动评论广播任务
    asyncio.create_task(broadcast_comments())
    
    return app

# 处理server关闭时终止所有爬虫进程
def handle_exit(signum, frame):
    logger.info("收到退出信号，正在关闭所有爬虫进程...")
    for proc in CRAWLER_PROCS:
        if proc.poll() is None:  # 进程还在运行
            try:
                proc.terminate()
                logger.info(f"已终止爬虫进程 PID={proc.pid}")
            except Exception as e:
                logger.error(f"终止爬虫进程失败: {e}")
    logger.info("所有爬虫进程已处理，server即将退出。")
    os._exit(0)

def main():
    """主函数"""
    # 加载配置
    load_config()
    # 注册信号处理
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    # 启动服务器
    app = init_app()
    web.run_app(app, host='localhost', port=8080)

if __name__ == '__main__':
    main() 