import aiosqlite
import jieba
import asyncio
from datetime import datetime, timezone, timedelta
import config
import os

# 定义东八区时区常量
TZ_8 = timezone(timedelta(hours=8))

# 提前初始化结巴分词，避免第一次运行时卡顿
jieba.initialize()

class DatabaseManager:
    def __init__(self):
        self.buffer = []
        self.lock = asyncio.Lock()  # 异步锁，防止并发写入时缓冲池冲突

    def get_db_path(self):
        """动态获取当前月份的数据库文件名"""
        import os
        month_str = datetime.now(TZ_8).strftime("%Y_%m")
        db_filename = f"chat_log_{month_str}.db"
        if config.DB_DIR and config.DB_DIR != '.':
            os.makedirs(config.DB_DIR, exist_ok=True)
        return os.path.join(config.DB_DIR, db_filename)

    async def init_db(self):
        """初始化当月数据库与表结构"""
        db_path = self.get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    user_id INTEGER,
                    sender_name TEXT,
                    message_thread_id INTEGER,
                    text TEXT,
                    file_id TEXT,
                    caption TEXT,
                    media_group_id TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 用于检索的 FTS5 虚拟表 (存储分词后的内容)
            await db.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    sender_name, text, caption
                )
            ''')
            await db.commit()

    async def add_to_buffer(self, msg_data):
        """将清洗后的消息加入内存缓冲池"""
        async with self.lock:
            self.buffer.append(msg_data)
            if len(self.buffer) >= config.BUFFER_LIMIT:
                await self.flush()

    async def flush(self):
        """将缓冲池内的数据批量写入 SQLite (带 Jieba 分词处理)"""
        async with self.lock:
            if not self.buffer:
                return  # 没数据就不操作

            db_path = self.get_db_path()
            # 跨月处理
            await self.init_db() 

            async with aiosqlite.connect(db_path) as db:
                for msg in self.buffer:
                    # 1. 写入原始数据
                    cursor = await db.execute('''
                        INSERT INTO messages (
                            message_id, user_id, sender_name, message_thread_id, 
                            text, file_id, caption, media_group_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        msg.get('message_id'), msg.get('user_id'), msg.get('sender_name'),
                        msg.get('message_thread_id'), msg.get('text'), msg.get('file_id'),
                        msg.get('caption'), msg.get('media_group_id')
                    ))
                    last_row_id = cursor.lastrowid

                    # 2. 用 jieba 分词 (搜索引擎模式) 处理需要检索的中文内容
                    seg_name = " ".join(jieba.lcut_for_search(msg.get('sender_name') or ""))
                    seg_text = " ".join(jieba.lcut_for_search(msg.get('text') or ""))
                    seg_caption = " ".join(jieba.lcut_for_search(msg.get('caption') or ""))

                    # 3. 写入 FTS5 检索表
                    await db.execute('''
                        INSERT INTO messages_fts (rowid, sender_name, text, caption)
                        VALUES (?, ?, ?, ?)
                    ''', (last_row_id, seg_name, seg_text, seg_caption))

                await db.commit()
            
            # 清空缓冲池
            self.buffer.clear()
            
    async def get_db_stats(self):
        """获取当前数据库的运行统计信息"""
        import os
        db_path = self.get_db_path()
        stats = {
            "db_name": db_path,
            "file_size_mb": 0.0,
            "total_messages": 0,
            "last_record_time": "暂无写入记录",
            "buffer_size": len(self.buffer)
        }
        
        if os.path.exists(db_path):
            stats["file_size_mb"] = round(os.path.getsize(db_path) / (1024 * 1024), 2)
            async with aiosqlite.connect(db_path) as db:
                try:
                    cursor = await db.execute("SELECT COUNT(*) FROM messages")
                    stats["total_messages"] = (await cursor.fetchone())[0]
                    
                    cursor = await db.execute("SELECT datetime(timestamp, '+8 hours') FROM messages ORDER BY id DESC LIMIT 1")
                    row = await cursor.fetchone()
                    if row:
                        stats["last_record_time"] = row[0]
                except Exception as e:
                    stats["last_record_time"] = f"读取异常: {e}"
        return stats

db_manager = DatabaseManager()