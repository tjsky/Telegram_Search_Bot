import os
import jieba
import aiosqlite
import html
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta  # 需安装: pip install python-dateutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes
from database import db_manager
import config

# 定义东八区时区常量
TZ_8 = timezone(timedelta(hours=8))

# --- 辅助函数 ---

def is_admin(user_id: int) -> bool:
    """权限校验"""
    return user_id in config.ADMIN_IDS

def get_db_path_by_offset(month_offset: int) -> str:
    """根据偏移量计算历史数据库文件名 (0=本月, 1=上个月)"""
    target_date = datetime.now(TZ_8) - relativedelta(months=month_offset)
    db_filename = f"chat_log_{target_date.strftime('%Y_%m')}.db"
    return os.path.join(config.DB_DIR, db_filename)

def build_fts_query(column: str, keyword: str) -> str:
    """将搜索词用 jieba 切分，并组装成 SQLite FTS5 识别的 MATCH 语句"""
    words = [w for w in jieba.lcut(keyword) if w.strip()]
    if not words:
        return ""
    # FTS5 语法：column : "词1" AND "词2"
    match_str = ' AND '.join([f'"{w}"' for w in words])
    return f'{column} : {match_str}'

# --- 核心搜索执行逻辑 ---

async def execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """执行数据库查询并渲染结果"""
    state = context.user_data.get('search_state')
    if not state:
        return

    db_path = get_db_path_by_offset(state['month_offset'])
    if not os.path.exists(db_path):
        text = f"⚠️ 找不到数据库文件 <code>{html.escape(db_path)}</code>。可能是该月无数据。"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode='HTML')
        else:
            await update.message.reply_text(text, parse_mode='HTML')
        return

    # 【新增逻辑】：判断使用全文检索引擎，还是 ID 精准匹配引擎
    is_fts = True
    sql_param = ""
    
    if state['type'] == 'text':
        match_query = build_fts_query('text', state['query'])
        match_query2 = build_fts_query('caption', state['query'])
        sql_param = f"({match_query}) OR ({match_query2})" if match_query2 else match_query
    elif state['type'] in ['username', 'name']:
        sql_param = build_fts_query('sender_name', state['query'])
    elif state['type'] == 'id':
        is_fts = False
        sql_param = state['query'] # 纯数字 ID，直接赋值

    limit = 10
    offset = (state['page'] - 1) * limit

    async with aiosqlite.connect(db_path) as db:
        try:
            if is_fts:
                # --- FTS5 全文模糊检索模式 ---
                cursor = await db.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?", (sql_param,))
                total_count = (await cursor.fetchone())[0]

                # 【变动】追加提取 m.user_id 和 m.message_id
                cursor = await db.execute('''
                    SELECT m.id, datetime(m.timestamp, '+8 hours'), m.sender_name, m.message_thread_id, m.text, m.caption, m.media_group_id, m.file_id, m.user_id, m.message_id
                    FROM messages m
                    JOIN messages_fts f ON m.id = f.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY m.timestamp DESC
                    LIMIT ? OFFSET ?
                ''', (sql_param, limit, offset))
            else:
                # --- ID 精准查询模式 ---
                cursor = await db.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (sql_param,))
                total_count = (await cursor.fetchone())[0]

                # 【变动】追加提取 user_id 和 message_id
                cursor = await db.execute('''
                    SELECT id, datetime(timestamp, '+8 hours'), sender_name, message_thread_id, text, caption, media_group_id, file_id, user_id, message_id
                    FROM messages
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                ''', (sql_param, limit, offset))
                
            rows = await cursor.fetchall()
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ 查询出错: {e}")
            return

    if total_count == 0:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="📭 抱歉，当前月份没有找到匹配的记录。")
        return

    # 3. 渲染单条长文本
    msg_text = f"🔍 <b>检索结果</b> (当月共 {total_count} 条)\n"
    msg_text += f"📅 库文件: <code>{html.escape(db_path)}</code>\n"
    msg_text += f"📄 页码: {state['page']} / {((total_count - 1) // limit) + 1}\n\n"
    
    # 提取纯数字的群组 ID，用于拼接消息直达链接 (例如: -1001234 变成 1234)
    chat_id_str = str(config.TARGET_CHAT_ID).replace('-100', '')
    
    current_count = 0
    for row in rows:
        # 【解包】接收新增加的 user_id 和 tg_msg_id
        db_id, ts, name, thread_id, text_content, caption, mg_id, f_id, user_id, tg_msg_id = row
        
        # 【需求3】剔除我们当初存入的 (@xxxx) 后缀，改为展示纯数字 ID
        display_name = name.split(' (@')[0] if name else "未知"
        safe_name = html.escape(display_name)
        record = f"👤 <b>{safe_name}</b> <code>({user_id})</code> "
        
        # 【需求2】话题名称映射与动态截断（最多取前 10 个字符）
        if thread_id:
            # 兼容 yaml 里 key 为数字或字符串的情况
            topic_name = config.TOPIC_MAPPING.get(thread_id) or config.TOPIC_MAPPING.get(str(thread_id))
            if topic_name:
                display_topic = topic_name[:10]
                record += f" <code>[话题：{html.escape(display_topic)}]</code> "
            else:
                record += f" <code>[话题: #{thread_id}]</code> "
        else:
            record += f" <code>[话题：默认]</code> "
        record += f"\n🕒 {ts}\n"

        # 处理内容截断
        content = text_content or caption or ""
        if len(content) > config.DISPLAY_TEXT_LENGTH:
            content = content[:config.DISPLAY_TEXT_LENGTH] + "...\n[单条展示过长已折叠]"
            
        safe_content = html.escape(content)
        
        # 【需求1】将 💬 图标变成可点击的超链接，直达 TG 原始群组消息
        msg_link = f"https://t.me/c/{chat_id_str}/{tg_msg_id}"
        record += f"<a href='{msg_link}'>💬</a> {safe_content}\n"

        # 媒体提取指令 (保持用 db_id，旧逻辑不变)
        if mg_id:
            record += f"🖼 [媒体组] 提取发: <code>/album {mg_id}</code>\n"
        elif f_id:
            record += f"🖼 [单媒体] 提取发: <code>/media {db_id}</code>\n"
        
        record += "─" * 20 + "\n"

        # 防爆破机制
        if len(msg_text) + len(record) > 3900:
            msg_text += "\n⚠️ 消息长度已达 Telegram 极限，本页剩余结果已隐藏。"
            break
        
        msg_text += record
        current_count += 1

    # 4. 构建翻页内联键盘
    keyboard = []
    nav_row = []
    if state['page'] > 1:
        nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data="nav:prev"))
    if offset + current_count < total_count:
        nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data="nav:next"))
    if nav_row:
        keyboard.append(nav_row)

    if total_count < 200:
        keyboard.append([InlineKeyboardButton("⏳ 当月结果较少，点此追溯上个月", callback_data="nav:cross_month")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    # 发送更新
    if update.callback_query:
        await update.callback_query.edit_message_text(text=msg_text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text=msg_text, reply_markup=reply_markup, parse_mode='HTML')

# --- 指令入口 ---

async def cmd_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /search 关键词"""
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("用法: <code>/search</code> 关键词" , parse_mode='HTML')
        return
    
    # 初始化状态存入 user_data，突破 callback_data 的 64 字节限制
    context.user_data['search_state'] = {
        'type': 'text',
        'query': " ".join(context.args),
        'page': 1,
        'month_offset': 0
    }
    await execute_search(update, context)

async def cmd_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /user 用户名 (针对 @xxxx)"""
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("用法: <code>/user</code> username (不带@，例如 /user xiaobaigou)", parse_mode='HTML')
        return
    
    context.user_data['search_state'] = {
        'type': 'username',
        'query': " ".join(context.args),
        'page': 1,
        'month_offset': 0
    }
    await execute_search(update, context)

async def cmd_search_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /name 姓名 (针对显示昵称 Fullname)"""
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("用法: <code>/name</code> 姓名 (例如 /name 大白狗)", parse_mode='HTML')
        return
    
    context.user_data['search_state'] = {
        'type': 'name',
        'query': " ".join(context.args),
        'page': 1,
        'month_offset': 0
    }
    await execute_search(update, context)
    
async def cmd_search_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /id 用户数字ID (终极追踪)"""
    if not is_admin(update.effective_user.id): return
    
    # 必须提供参数，且参数必须是纯数字
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法: <code>/id</code> 123456789 (必须提供纯数字的 User ID)", parse_mode='HTML')
        return
    
    context.user_data['search_state'] = {
        'type': 'id',
        'query': context.args[0],
        'page': 1,
        'month_offset': 0
    }
    await execute_search(update, context)

# --- 按钮回调处理 ---

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮点击"""
    query = update.callback_query
    await query.answer() # 消除按钮上的转圈圈

    state = context.user_data.get('search_state')
    if not state:
        await query.edit_message_text("⚠️ 搜索状态已过期，请重新发起搜索。")
        return

    action = query.data.split(":")[1]
    if action == "prev":
        state['page'] -= 1
    elif action == "next":
        state['page'] += 1
    elif action == "cross_month":
        state['month_offset'] += 1
        state['page'] = 1 # 跨月后页码重置

    await execute_search(update, context)

# --- 媒体还原功能 ---

async def cmd_get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """根据 message_id 还原单张媒体"""
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    msg_id = context.args[0]

    # 为了简单，直接去当月库查（可自行扩展跨月逻辑）
    db_path = get_db_path_by_offset(context.user_data.get('search_state', {}).get('month_offset', 0))
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT file_id, caption FROM messages WHERE id = ?", (msg_id,))
        row = await cursor.fetchone()
    
    if row and row[0]:
        # 尝试以照片发送，如果失败会被抛出异常，实际生产中可根据特征判断是视频还是图
        await update.message.reply_photo(photo=row[0], caption=row[1])

async def cmd_get_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """根据 media_group_id 还原相册"""
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    group_id = context.args[0]

    db_path = get_db_path_by_offset(context.user_data.get('search_state', {}).get('month_offset', 0))
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT file_id FROM messages WHERE media_group_id = ?", (group_id,))
        rows = await cursor.fetchall()

    if rows:
        media_group = [InputMediaPhoto(media=row[0]) for row in rows]
        # Telegram API 限制一个 media_group 最多 10 张
        await update.message.reply_media_group(media=media_group[:10])

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令 (显示运维仪表盘)"""
    # 终极静默防线：不是私聊直接扔，不是管理员直接扔
    if update.effective_chat.type != 'private': return
    if not is_admin(update.effective_user.id): return
    
    stats = await db_manager.get_db_stats()
    
    text = (
        "🤖 <b>TG 群组检索 Bot 已就绪</b>\n"
        "────────────────────\n"
        "📊 <b>系统运行状态</b>\n"
        f"📁 当前库文件: <code>{html.escape(stats['db_name'])}</code>\n"
        f"💾 库文件大小: <code>{stats['file_size_mb']} MB</code>\n"
        f"📝 当月记录数: <code>{stats['total_messages']} 条</code>\n"
        f"⏳ 缓冲池待写: <code>{stats['buffer_size']} 条</code> (每5秒自动落盘)\n"
        f"⏱️ 最新写入于: <code>{stats['last_record_time']}</code>\n"
        "────────────────────\n"
        "💡 发送 /help 查看所有可用检索指令。"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令 (显示指令大全)"""
    if update.effective_chat.type != 'private': return
    if not is_admin(update.effective_user.id): return
    
    text = (
        "🛠️ <b>管理员检索指令大全</b>\n"
        "────────────────────\n"
        "🔍 <b>内容检索</b>\n"
        "<code>/search</code> 关键词 - 全文检索聊天记录\n\n"
        "👤 <b>身份检索</b>\n"
        "<code>/user</code> username - 精准搜索 @用户名\n"
        "<code>/name</code> 昵称 - 模糊搜索用户的显示名称\n"
        "<code>/id</code> 12345678 - 终极追踪 (根据纯数字 ID)\n\n"
        "🖼️ <b>媒体提取</b> (检索结果中会提供单号)\n"
        "<code>/media</code> 媒体号 - 提取单张图片或视频\n"
        "<code>/album</code> 媒体组号 - 提取完整相册内容\n\n"
        "⚙️ <b>系统运维</b>\n"
        "<code>/start</code> - 查看数据库监控仪表盘\n"
        "<code>/help</code> - 显示本帮助菜单"
    )
    await update.message.reply_text(text, parse_mode='HTML')