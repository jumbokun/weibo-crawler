import asyncio
import json
import time
import traceback
import csv
from datetime import datetime
from playwright.async_api import async_playwright, Browser, Page

SPACE_URL = "https://x.com/i/broadcasts/1PlKQMkyLpMKE"
RESULT_FILE = "result.csv"
RAW_FILE = "raw_messages.txt"

class CommentCollector:
    def __init__(self):
        self.comments = []
        self.csv_headers = ['timestamp', 'username', 'display_name', 'content']
        self.ensure_csv_file()
    
    def ensure_csv_file(self):
        # 确保CSV文件存在并包含表头
        try:
            with open(RESULT_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.csv_headers)
        except Exception as e:
            print(f"[ERROR] 创建CSV文件失败: {str(e)}")

    def add_comment(self, timestamp, username, display_name, content):
        comment = {
            'timestamp': timestamp,
            'username': username,
            'display_name': display_name,
            'content': content
        }
        self.comments.append(comment)
        self.save_comment(comment)
        print(f"[已保存评论] {display_name}({username}): {content}")

    def save_comment(self, comment):
        try:
            with open(RESULT_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    comment['timestamp'],
                    comment['username'],
                    comment['display_name'],
                    comment['content']
                ])
        except Exception as e:
            print(f"[ERROR] 保存评论失败: {str(e)}")

    def save_all(self):
        if not self.comments:
            return
        
        print(f"\n[*] 保存{len(self.comments)}条评论到{RESULT_FILE}...")
        try:
            with open(RESULT_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.csv_headers)
                for comment in self.comments:
                    writer.writerow([
                        comment['timestamp'],
                        comment['username'],
                        comment['display_name'],
                        comment['content']
                    ])
            print("[*] 保存完成")
        except Exception as e:
            print(f"[ERROR] 保存所有评论失败: {str(e)}")

def save_raw_message(msg):
    try:
        with open(RAW_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{msg}\n")
    except Exception as e:
        print(f"[ERROR] 保存原始消息失败: {str(e)}")

async def monitor_websocket():
    print(f"[*] 开始访问页面: {SPACE_URL}")
    collector = CommentCollector()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, devtools=True)
        page = await browser.new_page()

        async def on_websocket(ws):
            if "chatnow" in ws.url:
                print(f"\n[找到聊天WebSocket] {ws.url}")
                
                def handle_message(data):
                    try:
                        msg = json.loads(data)
                        save_raw_message(data)
                        
                        # 解析评论消息
                        if msg.get("kind") == 1 and "payload" in msg:
                            try:
                                payload = json.loads(msg["payload"])
                                if "body" in payload:
                                    body = json.loads(payload["body"])
                                    text = body.get("body")
                                    display_name = body.get("displayName")
                                    username = body.get("username")
                                    timestamp = datetime.fromtimestamp(body.get("timestamp", 0)/1000).strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    if text and display_name and username:
                                        print(f"\n[收到评论] [{timestamp}] {display_name}(@{username}): {text}")
                                        collector.add_comment(timestamp, username, display_name, text)
                            except Exception as e:
                                print(f"[ERROR] 解析评论失败: {str(e)}")
                                print(f"[DEBUG] 原始消息: {data}")
                    except Exception as e:
                        print(f"[ERROR] 处理消息失败: {str(e)}")

                ws.on("framereceived", handle_message)

        page.on("websocket", on_websocket)

        try:
            await page.goto(SPACE_URL)
            print("[*] 页面加载完成")
            await page.wait_for_load_state("networkidle")
            
            try:
                continue_button = page.get_by_text("继续浏览")
                if await continue_button.is_visible():
                    print("[*] 点击'继续浏览'按钮...")
                    await continue_button.click()
            except:
                pass

            print("[*] 开始监听WebSocket消息...")
            print("[*] 按Ctrl+C停止程序...")
            print(f"[*] 原始消息将保存到 {RAW_FILE}")
            print(f"[*] 解析后的评论将保存到 {RESULT_FILE}")
            
            while True:
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[ERROR] {str(e)}")
            print(traceback.format_exc())
        finally:
            await browser.close()
            return collector

def main():
    print("[*] 开始监听Space聊天...")
    collector = None
    try:
        collector = asyncio.run(monitor_websocket())
    except KeyboardInterrupt:
        print("\n[*] 收到中断信号")
    except Exception as e:
        print(f"[ERROR] {str(e)}")
    finally:
        if collector:
            collector.save_all()
        print("[*] 程序结束")

if __name__ == "__main__":
    main()
