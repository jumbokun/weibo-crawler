#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import logging.config
import os
import random
import sqlite3
from collections import OrderedDict
from time import sleep
import requests
from requests.adapters import HTTPAdapter
import re
from datetime import datetime
from weibo import Weibo
import sys
import signal
import colorama
from colorama import Fore, Style
import time
from queue import Queue
from threading import Thread
from queue import Empty

# 初始化colorama
colorama.init()

# 配置日志
if not os.path.isdir("log/"):
    os.makedirs("log/")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 全局变量用于控制程序运行
running = True

def print_banner():
    """打印程序启动横幅"""
    banner = f"""
{Fore.CYAN}微博弹幕助手 v1.0{Style.RESET_ALL}
{Fore.YELLOW}--------------------------------{Style.RESET_ALL}
请确保:
1. config.json 中已设置正确的 cookie
2. OBS 已经准备就绪
{Fore.YELLOW}--------------------------------{Style.RESET_ALL}
"""
    print(banner)

def print_success_message(port=8080):
    """打印成功消息和OBS设置指南"""
    message = f"""
{Fore.GREEN}弹幕系统已成功启动！{Style.RESET_ALL}

{Fore.YELLOW}OBS设置指南：{Style.RESET_ALL}
1. 在OBS中添加"浏览器"源
2. 输入以下URL：
   {Fore.CYAN}http://localhost:{port}/danmu.html{Style.RESET_ALL}
3. 设置宽度：400
4. 设置高度：600

{Fore.YELLOW}提示：{Style.RESET_ALL}
- 程序会持续获取最新评论
- 按 Ctrl+C 可以停止程序
- 弹幕显示可以在 danmu_config.json 中调整
"""
    print(message)

def signal_handler(signum, frame):
    """处理Ctrl+C信号"""
    global running
    print(f"\n{Fore.YELLOW}正在停止程序...{Style.RESET_ALL}")
    running = False

def get_weibo_id_by_bid(bid, cookie):
    """通过BID获取微博ID"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36",
        "Cookie": cookie
    }
    
    try:
        # 使用新的API直接获取微博信息
        url = f"https://weibo.com/ajax/statuses/show?id={bid}"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'id' in data:
            logger.info(f"成功将BID {bid} 转换为微博ID {data['id']}")
            return str(data['id'])
        else:
            logger.error(f"未能从API响应中获取到微博ID: {data}")
            return None
            
    except Exception as e:
        logger.error(f"转换微博ID失败: {e}")
        return None

class WeiboCommentCrawler:
    def __init__(self, bid, cookie):
        # 先转换BID为微博ID
        self.bid = bid
        weibo_id = get_weibo_id_by_bid(bid, cookie)
        if not weibo_id:
            raise ValueError(f"无法获取BID为 {bid} 的微博ID")
            
        self.weibo_id = weibo_id
        self.cookie = cookie
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36",
            "Cookie": cookie
        }
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=5)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.known_comment_ids = set()  # 用于跟踪已知评论
        
        logger.info(f"初始化爬虫: BID={bid}, 微博ID={weibo_id}")

    def get_comments(self, batch_size=10):
        """获取指定微博的评论
        
        Args:
            batch_size: 每次获取的评论数量
        """
        weibo = {"id": self.weibo_id}
        all_comments = []
        
        def comment_callback(_, new_comments):
            all_comments.extend(new_comments)
        
        self._get_weibo_comments_cookie(weibo, 0, batch_size, None, comment_callback)
        
        # 过滤掉已知的评论
        new_comments = []
        for comment in all_comments:
            if comment['id'] not in self.known_comment_ids:
                new_comments.append(comment)
                self.known_comment_ids.add(comment['id'])
        
        return new_comments

    def _get_weibo_comments_cookie(self, weibo, cur_count, max_count, max_id, on_downloaded):
        """获取评论的核心方法"""
        if cur_count >= max_count:
            return

        url = "https://weibo.com/ajax/statuses/buildComments"
        params = {
            "id": weibo["id"],
            "flow": 1,  # 1表示按时间排序
            "is_reload": 1,
            "is_show_bulletin": 2,
            "is_mix": 0,
            "count": 20,  # 每次获取20条评论
            "fetch_level": 0,
            "locale": "zh-CN"
        }
        
        if max_id:
            params["page"] = max_id
        
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                response = self.session.get(url, params=params, headers=self.headers, timeout=10)
                response.raise_for_status()
                json_data = response.json()

                if 'data' not in json_data:
                    logger.warning(f"未获取到评论数据，正在进行第 {retry_count + 1} 次重试...")
                    retry_count += 1
                    sleep(5)
                    continue

                comments = json_data.get('data', [])
                count = len(comments)
                
                if count == 0:
                    return

                if on_downloaded:
                    on_downloaded(weibo, comments)

                # 随机睡眠以避免被限制
                sleep(random.uniform(1, 2))

                cur_count += count
                next_page = json_data.get('max_id', 0) != 0 or (max_id or 1) * 20 < json_data.get('total_number', 0)

                if not next_page:
                    return

                # 递归获取下一页评论
                self._get_weibo_comments_cookie(weibo, cur_count, max_count, (max_id or 1) + 1, on_downloaded)
                return  # 成功获取评论后返回

            except requests.exceptions.RequestException as e:
                logger.error(f"网络请求失败: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"等待10秒后进行第 {retry_count + 1} 次重试...")
                    sleep(10)
                else:
                    logger.error("网络请求重试次数已达上限")
                    return
                    
            except json.JSONDecodeError as e:
                logger.error(f"解析响应数据失败: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"等待10秒后进行第 {retry_count + 1} 次重试...")
                    sleep(10)
                else:
                    logger.error("JSON解析重试次数已达上限")
                    return
                    
            except Exception as e:
                logger.error(f"获取评论时发生未知错误: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"等待15秒后进行第 {retry_count + 1} 次重试...")
                    sleep(10)
                else:
                    logger.error("重试次数已达上限")
                    return

    def _parse_comment(self, comment):
        """解析评论数据"""
        if not comment:
            return None
            
        sqlite_comment = OrderedDict()
        sqlite_comment["id"] = comment["id"]
        sqlite_comment["weibo_id"] = self.weibo_id
        sqlite_comment["user_screen_name"] = comment.get("user", {}).get("screen_name", "")
        
        # 移除HTML标签
        text = re.sub('<[^<]+?>', '', comment.get("text", "")).replace('\n', '').strip()
        sqlite_comment["text"] = text
        
        # 转换时间格式
        created_at = comment.get("created_at", "")
        if created_at:
            try:
                # 如果已经是标准格式，直接使用
                if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', created_at):
                    pass
                # 否则转换微博时间格式
                else:
                    # 将微博时间格式转换为datetime对象
                    dt = datetime.strptime(created_at, '%a %b %d %H:%M:%S +0800 %Y')
                    # 转换为标准的ISO格式
                    created_at = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logger.error(f"时间格式转换失败: {e}, 原始时间: {created_at}")
                created_at = ""
        
        sqlite_comment["created_at"] = created_at
        
        return sqlite_comment

    def save_to_sqlite(self, comments):
        """将评论保存到SQLite数据库"""
        if not comments:
            return

        db_path = "./weibo/weibodata.db"
        con = sqlite3.connect(db_path)
        
        for comment in comments:
            data = self._parse_comment(comment)
            if data:
                self._insert_comment(con, data)
        
        con.close()
        logger.info(f"已将 {len(comments)} 条新评论保存到数据库")

    def save_to_json(self, comments):
        """将评论保存到JSON文件"""
        if not comments:
            return

        os.makedirs("./weibo", exist_ok=True)
        json_path = f"./weibo/comments_{self.weibo_id}.json"
        
        # 转换评论数据为可序列化的格式
        json_comments = []
        for comment in comments:
            data = self._parse_comment(comment)
            if data:
                json_comments.append(data)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_comments, f, ensure_ascii=False, indent=2)
        
        logger.info(f"已将 {len(comments)} 条评论保存到 {json_path}")

    def _insert_comment(self, con, data):
        """将评论插入数据库"""
        cur = con.cursor()
        keys = ",".join(data.keys())
        values = ",".join(["?"] * len(data))
        sql = f"""INSERT OR REPLACE INTO comments({keys}) VALUES({values})"""
        cur.execute(sql, list(data.values()))
        con.commit()

def check_config():
    """检查配置文件是否存在且包含必要的信息"""
    if not os.path.exists("config.json"):
        example_config = {
            "cookie": "在这里填入你的微博cookie",
        }
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(example_config, f, ensure_ascii=False, indent=2)
        print(f"{Fore.RED}错误：未找到config.json文件{Style.RESET_ALL}")
        print(f"已创建示例配置文件，请编辑 {Fore.CYAN}config.json{Style.RESET_ALL} 并填入你的cookie")
        return False
    
    try:
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
            if not config.get("cookie"):
                print(f"{Fore.RED}错误：config.json中未设置cookie{Style.RESET_ALL}")
                return False
    except Exception as e:
        print(f"{Fore.RED}错误：读取config.json失败 - {e}{Style.RESET_ALL}")
        return False
    
    return True

def main():
    try:
        # 打印启动横幅
        print_banner()

        # 检查配置
        if not check_config():
            input("\n按回车键退出...")
            return

        # 注册信号处理器
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # 获取命令行参数
        if len(sys.argv) < 2:
            print(f"{Fore.YELLOW}请输入微博BID：{Style.RESET_ALL}", end="")
            target_bid = input().strip()
        else:
            target_bid = sys.argv[1]

        if not target_bid:
            print(f"{Fore.RED}错误：未提供微博BID{Style.RESET_ALL}")
            input("\n按回车键退出...")
            return

        # 从config.json读取配置
        try:
            with open("config.json", encoding="utf-8") as f:
                config = json.load(f)
                cookie = config.get("cookie", "")
        except Exception as e:
            print(f"{Fore.RED}错误：读取配置文件失败 - {e}{Style.RESET_ALL}")
            input("\n按回车键退出...")
            return

        batch_size = 10  # 每次获取300条评论

        print(f"\n{Fore.CYAN}正在查找BID为 {target_bid} 的微博...{Style.RESET_ALL}")
        
        try:
            # 创建爬虫实例（内部会自动转换BID为微博ID）
            crawler = WeiboCommentCrawler(target_bid, cookie)
            print(f"{Fore.GREEN}已找到对应的微博ID: {crawler.weibo_id}{Style.RESET_ALL}")
        except ValueError as e:
            print(f"{Fore.RED}错误：{e}{Style.RESET_ALL}")
            input("\n按回车键退出...")
            return

        # 创建数据库目录和表
        db_path = "./weibo/weibodata.db"
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        create_comments_table = """
        CREATE TABLE IF NOT EXISTS comments (
            id varchar(20) NOT NULL,
            weibo_id varchar(32) NOT NULL,
            user_screen_name varchar(64) NOT NULL,
            text varchar(1000),
            created_at varchar(20),
            PRIMARY KEY (id)
        );
        """
        cur.execute(create_comments_table)
        con.commit()
        con.close()

        print(f"\n{Fore.CYAN}开始获取评论...{Style.RESET_ALL}")
        print_success_message()
        
        try:
            while running:
                new_comments = crawler.get_comments(batch_size)
                if new_comments:
                    print(f"{Fore.GREEN}获取到 {len(new_comments)} 条新评论{Style.RESET_ALL}")
                    crawler.save_to_sqlite(new_comments)
                
                sleep(3)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}程序已停止{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}程序发生错误: {e}{Style.RESET_ALL}")
            logger.exception("程序异常")
        finally:
            print(f"\n{Fore.YELLOW}评论获取已完成{Style.RESET_ALL}")
            input("\n按回车键退出...")

    except Exception as e:
        print(f"\n{Fore.RED}发生严重错误: {e}{Style.RESET_ALL}")
        logger.exception("严重错误")
        input("\n按回车键退出...")

if __name__ == "__main__":
    main() 