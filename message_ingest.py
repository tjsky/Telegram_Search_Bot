import config
from database import db_manager
from telegram import Update
from telegram.ext import ContextTypes

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理群组内的所有常规消息"""
    
    # 1. 非白名单群组自动退群并丢弃消息
    chat = update.effective_chat
    if chat.id != config.TARGET_CHAT_ID:
        if chat.type in ['group', 'supergroup']:
            print(f"⚠️ 警告：Bot 被拉入非授权群组 {chat.id}，正在执行自动退群...")
            await chat.leave()
        return

    msg = update.effective_message
    if not msg:
        return

    # 2. 提取身份信息与话题状态
    user = msg.from_user
    sender_name = user.first_name
    if user.last_name:
        sender_name += f" {user.last_name}"
    if user.username:
        sender_name += f" (@{user.username})"

    # 3. 话题群组记录对应的话题 ID
    message_thread_id = msg.message_thread_id if msg.is_topic_message else None

    # 4. 提取文本内容
    raw_text = msg.text or msg.caption or ""

    # 5. 截断文字 防止超出 TG 的 4096 限制
    truncate_suffix = "\n[文本过长已截断]"
    if len(raw_text) > config.MAX_TEXT_LENGTH:
        safe_length = config.MAX_TEXT_LENGTH - len(truncate_suffix)
        raw_text = raw_text[:safe_length] + truncate_suffix

    # 6. 提取媒体文件的 file_id
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id 
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.document:
        file_id = msg.document.file_id
    elif msg.animation: 
        file_id = msg.animation.file_id

    # 7. 排除系统消息、贴纸、故事、动态等特殊消息
    if not raw_text and not file_id:
        return

    # 8. 推送至缓冲池
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


    await db_manager.add_to_buffer(msg_data)