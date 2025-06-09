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
import time

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

# 在全局变量部分添加
CRAWLER_TASKS = {}  # 存储爬虫任务的字典

# 新增：管理当前活跃的评论爬虫进程
ACTIVE_COMMENT_PROCS = {}

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
    # 只在新连接时刷新爬虫
    await manage_crawlers_on_ws_change()
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
    # 新增：新连接时主动推送最新几条评论
    try:
        latest_comments = await get_latest_comments()
        # 先清除现有评论
        await ws.send_json({'type': 'clear', 'payload': {}})
        for comment in latest_comments:
            await ws.send_json({
                'type': 'danmu',
                'payload': {
                    'username': comment['username'],
                    'message': comment['message']
                }
            })
    except Exception as e:
        logger.error(f"主动推送评论失败: {e}")
    try:
        heartbeat_task = asyncio.create_task(send_heartbeat(ws))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.PONG:
                logger.debug("收到客户端心跳响应")
                continue
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f'WebSocket连接错误: {ws.exception()}')
                break
    finally:
        WEBSOCKET_CLIENTS.remove(ws)
        heartbeat_task.cancel()
        logger.info(f"WebSocket连接关闭，当前连接数: {len(WEBSOCKET_CLIENTS)}")
        # 断开时不再刷新爬虫
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
    return web.FileResponse('./danmu.html')

async def reload_config():
    """重新加载配置并重启相关任务"""
    global DANMU_CONFIG
    
    try:
        # 重新加载弹幕配置
        load_config()
        
        # 重新加载主配置
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        # 更新所有WebSocket客户端的样式
        for ws in WEBSOCKET_CLIENTS.copy():
            try:
                await ws.send_json({
                    'type': 'style',
                    'payload': {
                        'containerWidth': DANMU_CONFIG['container_width'],
                        'fontSize': DANMU_CONFIG['font_size'],
                        'textColor': DANMU_CONFIG['text_color'],
                        'lines': DANMU_CONFIG['max_comments']
                    }
                })
            except Exception as e:
                logger.error(f"更新客户端样式失败: {e}")
                WEBSOCKET_CLIENTS.remove(ws)
        
        # 重启所有正在运行的爬虫任务
        for bid, task in CRAWLER_TASKS.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                
                # 重新启动爬虫
                new_task = asyncio.create_task(start_crawler_for_bid(bid))
                CRAWLER_TASKS[bid] = new_task
                
        logger.info("配置重载完成")
        return True
    except Exception as e:
        logger.error(f"重载配置失败: {e}")
        return False

async def start_crawler_for_bid(bid):
    """为指定的BID启动爬虫"""
    try:
        # 读取配置
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        # 构建爬虫命令
        cmd = ['python', 'weibo.py', '--bid', bid]
        if config.get('only_crawl_original'):
            cmd.append('--only-crawl-original')
            
        # 启动爬虫进程
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # 等待进程完成
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"爬虫进程异常退出: {stderr.decode()}")
            
    except Exception as e:
        logger.error(f"启动爬虫失败: {e}")

async def update_cookie(request):
    """处理Cookie更新请求"""
    try:
        data = await request.json()
        cookie = data.get('cookie')
        
        if not cookie:
            return web.json_response({
                'success': False,
                'message': 'Cookie不能为空'
            }, status=400)
            
        # 读取现有配置
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return web.json_response({
                'success': False,
                'message': '读取配置文件失败'
            }, status=500)
            
        # 更新cookie
        config['cookie'] = cookie
        
        # 保存更新后的配置
        try:
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return web.json_response({
                'success': False,
                'message': '保存配置文件失败'
            }, status=500)
            
        # 重新加载配置
        if await reload_config():
            logger.info("Cookie更新并重载成功")
            return web.json_response({
                'success': True,
                'message': 'Cookie更新成功，配置已重载'
            })
        else:
            return web.json_response({
                'success': False,
                'message': 'Cookie更新成功，但配置重载失败'
            }, status=500)
            
    except Exception as e:
        logger.error(f"更新Cookie失败: {e}")
        return web.json_response({
            'success': False,
            'message': f'更新Cookie失败: {str(e)}'
        }, status=500)

async def serve_cookie_manager(request):
    """提供Cookie管理页面"""
    return web.FileResponse('./cookie_manager.html')

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
    """启动爬虫"""
    try:
        data = await request.json()
        input_str = data.get('input', '').strip()
        
        if not input_str:
            return web.json_response({
                'success': False,
                'message': '请输入微博链接或ID'
            }, status=400)
            
        # 提取ID
        bid = extract_id_from_input(input_str)
        if not bid:
            return web.json_response({
                'success': False,
                'message': '无法解析输入的微博链接或ID'
            }, status=400)
            
        # 如果已经有相同BID的任务在运行，先取消它
        if bid in CRAWLER_TASKS and not CRAWLER_TASKS[bid].done():
            CRAWLER_TASKS[bid].cancel()
            try:
                await CRAWLER_TASKS[bid]
            except asyncio.CancelledError:
                pass
                
        # 启动新的爬虫任务
        task = asyncio.create_task(start_crawler_for_bid(bid))
        CRAWLER_TASKS[bid] = task
        
        return web.json_response({
            'success': True,
            'message': f'已启动爬虫任务，监控BID: {bid}'
        })
        
    except Exception as e:
        logger.error(f"启动爬虫失败: {e}")
        return web.json_response({
            'success': False,
            'message': f'启动爬虫失败: {str(e)}'
        }, status=500)

async def init_app():
    """初始化应用"""
    app = web.Application()
    
    # 添加路由
    app.router.add_get('/', serve_index_html)
    app.router.add_get('/danmu.html', serve_danmu_html)
    app.router.add_get('/danmu', serve_danmu_html)
    app.router.add_get('/cookie', serve_cookie_manager)  # 新增Cookie管理页面路由
    app.router.add_post('/update_cookie', update_cookie)  # 新增Cookie更新路由
    app.router.add_get('/ws', websocket_handler)
    app.router.add_post('/start_crawler', start_crawler)
    
    # 加载配置
    load_config()
    
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
    # 注册信号处理
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    # 启动服务器
    app = init_app()
    web.run_app(app, host='localhost', port=8080)

# 新增：获取指定用户最新5条微博ID

def get_latest_5_weibo_ids(user_id, cookie):
    """通过微博API获取指定用户最新5条微博ID（修正版）"""
    url = f'https://m.weibo.cn/api/container/getIndex?type=uid&value={user_id}&containerid=107603{user_id}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36',
        'Cookie': cookie
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cards = data.get('data', {}).get('cards', [])
        weibo_ids = []
        for card in cards:
            if card.get('card_type') == 9 and 'mblog' in card:
                weibo_ids.append(str(card['mblog']['id']))
                if len(weibo_ids) >= 5:
                    break
        return weibo_ids
    except Exception as e:
        logger.error(f"获取最新5条微博ID失败: {e}")
        return []

# 新增：启动评论爬虫进程

def start_comment_crawlers(weibo_ids, cookie):
    global ACTIVE_COMMENT_PROCS
    stop_comment_crawlers()  # 先停掉所有旧的
    ACTIVE_COMMENT_PROCS = {}
    for wid in weibo_ids:
        try:
            proc = subprocess.Popen([
                'python', 'get_single_weibo_comments.py', wid
            ], env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            ACTIVE_COMMENT_PROCS[wid] = proc
            logger.info(f"已启动评论爬虫进程，微博ID={wid}，PID={proc.pid}")
        except Exception as e:
            logger.error(f"启动评论爬虫进程失败: {e}")

# 新增：停止所有评论爬虫进程

def stop_comment_crawlers():
    global ACTIVE_COMMENT_PROCS
    for wid, proc in ACTIVE_COMMENT_PROCS.items():
        if proc.poll() is None:
            try:
                proc.terminate()
                logger.info(f"已终止评论爬虫进程，微博ID={wid}，PID={proc.pid}")
            except Exception as e:
                logger.error(f"终止评论爬虫进程失败: {e}")
    ACTIVE_COMMENT_PROCS = {}

# 新增：WebSocket连接管理，自动启动/停止爬虫

async def manage_crawlers_on_ws_change():
    """根据WebSocket连接数自动管理爬虫"""
    # 读取用户ID、cookie和n
    try:
        with open('config.json', encoding='utf-8') as f:
            config = json.load(f)
        user_id_file = config.get('user_id_list', 'user_id.txt')
        cookie = config.get('cookie', '')
        n = int(config.get('latest_weibo_count', 5))
        with open(user_id_file, encoding='utf-8') as f:
            user_id = f.read().strip().split()[0]
    except Exception as e:
        logger.error(f"读取用户ID、cookie或n失败: {e}")
        return
    if WEBSOCKET_CLIENTS:
        # 有连接，获取最新n条微博ID并启动爬虫
        weibo_ids = get_latest_n_weibo_ids(user_id, cookie, n)
        if weibo_ids:
            start_comment_crawlers(weibo_ids, cookie)
        else:
            logger.warning("未获取到最新微博ID，爬虫未启动")
    else:
        # 无连接，停止所有爬虫
        stop_comment_crawlers()

def get_latest_n_weibo_ids(user_id, cookie, n):
    """通过微博API获取指定用户最新n条微博ID（n从配置读取）"""
    url = f'https://m.weibo.cn/api/container/getIndex?type=uid&value={user_id}&containerid=107603{user_id}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36',
        'Cookie': cookie
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cards = data.get('data', {}).get('cards', [])
        weibo_ids = []
        for card in cards:
            if card.get('card_type') == 9 and 'mblog' in card:
                weibo_ids.append(str(card['mblog']['id']))
                if len(weibo_ids) >= n:
                    break
        return weibo_ids
    except Exception as e:
        logger.error(f"获取最新{n}条微博ID失败: {e}")
        return []

if __name__ == '__main__':
    main() 