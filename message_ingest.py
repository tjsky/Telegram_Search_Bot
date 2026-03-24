import config
from database import db_manager
from telegram import Update
from telegram.ext import ContextTypes

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理群组内的所有常规消息"""
    
    # 1. 隐私安全红线：非白名单群组自动退群并丢弃消息
    chat = update.effective_chat
    if chat.id != config.TARGET_CHAT_ID:
        print(f"⚠️ 警告：Bot 被拉入非授权群组 {chat.id} ({chat.title})，正在执行自动退群...")
        await chat.leave()
        return

    msg = update.effective_message
    if not msg:
        return

    # 2. 提取身份信息与话题状态 (Topic)
    user = msg.from_user
    # 组装发送者历史快照名称，例如: "小白猫 (@xiaobai)"
    sender_name = user.first_name
    if user.last_name:
        sender_name += f" {user.last_name}"
    if user.username:
        sender_name += f" (@{user.username})"

    # 如果群组开启了话题(Forum)，记录对应的话题 ID
    message_thread_id = msg.message_thread_id if msg.is_topic_message else None

    # 3. 提取文本内容 (自动识别是纯文本还是媒体的配套文字)
    raw_text = msg.text or msg.caption or ""

    # 4. 精准截断逻辑：小心计算，防止加上提示后超出 TG 的 4096 限制
    truncate_suffix = "\n[文本过长已截断]"
    if len(raw_text) > config.MAX_TEXT_LENGTH:
        # 预留出后缀的长度
        safe_length = config.MAX_TEXT_LENGTH - len(truncate_suffix)
        raw_text = raw_text[:safe_length] + truncate_suffix

    # 5. 提取媒体文件的 file_id
    file_id = None
    if msg.photo:
        # msg.photo 是一个数组，包含了不同清晰度的图片，[-1] 代表取最高清晰度
        file_id = msg.photo[-1].file_id 
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.document:
        file_id = msg.document.file_id
    elif msg.animation: # GIF 动图
        file_id = msg.animation.file_id

    # 如果既没有文字，也没有我们关注的媒体文件（比如它是一个系统消息或贴纸），则不记录
    if not raw_text and not file_id:
        return

    # 6. 组装最终数据字典，并推入数据库缓冲池
    msg_data = {
        'message_id': msg.message_id,
        'user_id': user.id,
        'sender_name': sender_name,
        'message_thread_id': message_thread_id,
        'text': raw_text if msg.text else "",
        'caption': raw_text if msg.caption else "",
        'file_id': file_id,
        'media_group_id': msg.media_group_id
    }

    # 交给 database.py 处理，如果是第 50 条会自动触发落盘
    await db_manager.add_to_buffer(msg_data)