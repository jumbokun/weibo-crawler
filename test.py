#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import os
import requests
from requests.adapters import HTTPAdapter
import sys
from time import sleep
import random
import re
from datetime import datetime
import colorama
from colorama import Fore, Style
import time

# 初始化colorama
colorama.init()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WeiboCommentTester:
    def __init__(self, weibo_id, cookie):
        self.weibo_id = weibo_id
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36",
            "Cookie": cookie
        }
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=3)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.last_request_time = 0
        self.min_request_interval = 0.5

    def get_comments(self, limit=10):
        """获取指定数量的最新评论（按时间排序）
        
        Args:
            limit: 获取的评论数量
        Returns:
            list: 评论列表
        """
        all_comments = []
        page = 1
        
        while len(all_comments) < limit:
            try:
                comments = self._fetch_comments_page(page)
                if not comments or not comments['data']:
                    break
                
                all_comments.extend(comments['data'])
                
                if not comments['next_page']:
                    break
                    
                page += 1
                # 智能等待
                sleep(random.uniform(0.5, 1))
                
            except Exception as e:
                logger.error(f"获取评论失败: {e}")
                break
        
        # 处理评论数据
        return self._process_comments(all_comments[:limit])

    def _fetch_comments_page(self, page):
        """获取单页评论"""
        url = "https://weibo.com/ajax/statuses/buildComments"
        params = {
            "id": 5159373685133472,
            "flow": 1,  # 1表示按时间排序
            "is_reload": 1,
            "is_show_bulletin": 2,
            "is_mix": 0,
            "count": 20,  # 每页20条评论
            "fetch_level": 0,
            "locale": "zh-CN"
        }
        
        if page > 1:
            params["page"] = page
        
        try:
            # 控制请求频率
            current_time = time.time()
            time_since_last_request = current_time - self.last_request_time
            if time_since_last_request < self.min_request_interval:
                sleep(self.min_request_interval - time_since_last_request)
            
            self.last_request_time = time.time()
            response = self.session.get(url, params=params, headers=self.headers, timeout=5)
            response.raise_for_status()
            json_data = response.json()
            
            if 'data' not in json_data:
                logger.error(f"返回数据格式错误: {json_data}")
                return None
            
            return {
                'data': json_data['data'],
                'next_page': json_data.get('max_id', 0) != 0 or page * 20 < json_data.get('total_number', 0)
            }
            
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return None

    def _process_comments(self, comments):
        """处理评论数据"""
        processed = []
        for comment in comments:
            try:
                # 提取需要的字段
                processed_comment = {
                    'id': comment['id'],
                    'user_name': comment['user']['screen_name'],
                    'text': re.sub('<[^<]+?>', '', comment['text']).strip(),  # 移除HTML标签
                    'created_at': self._format_time(comment['created_at']),
                    'like_count': comment.get('like_counts', 0),
                    'reply_count': comment.get('total_number', 0)
                }
                processed.append(processed_comment)
            except Exception as e:
                logger.error(f"处理评论数据失败: {e}")
                continue
                
        # 按时间排序
        processed.sort(key=lambda x: x['created_at'], reverse=True)
        return processed

    def _format_time(self, time_str):
        """格式化时间字符串"""
        try:
            if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', time_str):
                return time_str
            dt = datetime.strptime(time_str, '%a %b %d %H:%M:%S +0800 %Y')
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return time_str

def save_to_json(data, filename):
    """保存数据到JSON文件"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"{Fore.GREEN}数据已保存到 {filename}{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}保存数据失败: {e}{Style.RESET_ALL}")

def main():
    # 检查命令行参数
    if len(sys.argv) < 2:
        print(f"{Fore.YELLOW}请输入微博ID：{Style.RESET_ALL}", end="")
        weibo_id = input().strip()
    else:
        weibo_id = sys.argv[1]

    # 获取评论数量
    print(f"{Fore.YELLOW}请输入要获取的评论数量（默认10条）：{Style.RESET_ALL}", end="")
    try:
        limit = int(input().strip() or "10")
    except ValueError:
        limit = 10

    # 读取配置文件
    try:
        with open("config.json", encoding='utf-8') as f:
            config = json.load(f)
            cookie = config.get("cookie", "")
    except Exception as e:
        print(f"{Fore.RED}读取配置文件失败: {e}{Style.RESET_ALL}")
        return

    if not cookie:
        print(f"{Fore.RED}错误：未设置cookie{Style.RESET_ALL}")
        return

    print(f"\n{Fore.CYAN}开始获取微博评论...{Style.RESET_ALL}")
    
    # 创建测试实例并获取评论
    tester = WeiboCommentTester(weibo_id, cookie)
    comments = tester.get_comments(limit)
    
    if comments:
        print(f"{Fore.GREEN}成功获取 {len(comments)} 条评论{Style.RESET_ALL}")
        
        # 保存到JSON文件
        filename = f"comments_{weibo_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        save_to_json(comments, filename)
        
        # 打印评论预览
        print(f"\n{Fore.CYAN}评论预览：{Style.RESET_ALL}")
        for i, comment in enumerate(comments, 1):
            print(f"\n{i}. {Fore.YELLOW}{comment['user_name']}{Style.RESET_ALL} "
                  f"({comment['created_at']}):")
            print(f"   {comment['text']}")
            print(f"   点赞: {comment['like_count']} | 回复: {comment['reply_count']}")
    else:
        print(f"{Fore.RED}未获取到任何评论{Style.RESET_ALL}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}程序已停止{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}程序发生错误: {e}{Style.RESET_ALL}")
        logger.exception("程序异常")