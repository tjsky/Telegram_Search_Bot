import logging
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, CallbackQueryHandler
import config
from database import db_manager
from message_ingest import handle_group_message
from search_handler import (
    cmd_search_text, cmd_search_user, cmd_search_name, cmd_search_id, 
    handle_pagination, cmd_get_media, cmd_get_album,
    cmd_start, cmd_help  
)

# 配置基础日志，方便你在控制台/systemd日志里查看运行状态
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def flush_db_job(context):
    """定时任务：每 5 秒被调用一次，将内存里的零星数据写入数据库"""
    await db_manager.flush()

async def post_stop(application):
    """优雅退出钩子 (Graceful Shutdown)
    当 systemd 发送 SIGTERM 信号，或你按下 Ctrl+C 时，会触发此函数。
    """
    logger.info("🛑 收到系统停止信号，正在将缓冲池内的剩余数据强制落盘...")
    await db_manager.flush()
    logger.info("✅ 数据库落盘完毕，文件安全闭合。Bot 进程退出。")

def main():
    # 1. 初始化 Bot 应用，并挂载优雅退出钩子
    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_stop(post_stop)
        .build()
    )

    # 2. 注册消息收集处理器
    # 使用 ~filters.COMMAND 排除掉以 / 开头的命令，因为命令是留给私聊检索的
    ingest_filter = (
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION) 
        & (~filters.COMMAND)
    )
    application.add_handler(MessageHandler(ingest_filter, handle_group_message))
    private_chat_only = filters.ChatType.PRIVATE
    # 3. 注册系统运维指令
    application.add_handler(CommandHandler("start", cmd_start, filters=private_chat_only))
    application.add_handler(CommandHandler("help", cmd_help, filters=private_chat_only))

    # 4. 注册私聊检索指令 (加上 filters=private_chat_only)
    application.add_handler(CommandHandler("search", cmd_search_text, filters=private_chat_only))
    application.add_handler(CommandHandler("user", cmd_search_user, filters=private_chat_only))
    application.add_handler(CommandHandler("name", cmd_search_name, filters=private_chat_only))
    application.add_handler(CommandHandler("id", cmd_search_id, filters=private_chat_only))
    
    # 5. 注册媒体提取指令 (加上 filters=private_chat_only)
    application.add_handler(CommandHandler("media", cmd_get_media, filters=private_chat_only))
    application.add_handler(CommandHandler("album", cmd_get_album, filters=private_chat_only))
    
    # 6. 注册翻页按钮回调
    application.add_handler(CallbackQueryHandler(handle_pagination, pattern="^nav:"))
    
    # 7. 挂载定时任务 (JobQueue)
    # 每隔 config.FLUSH_INTERVAL(5秒) 执行一次 flush_db_job
    job_queue = application.job_queue
    job_queue.run_repeating(flush_db_job, interval=config.FLUSH_INTERVAL, first=config.FLUSH_INTERVAL)

    logger.info("🚀 Bot 引擎启动成功！正在监听 12 万人群组消息...")
    logger.info("💡 提示：按 Ctrl+C 关闭程序时，可以观察优雅退出的日志。")
    
    # 7. 启动长轮询 (run_polling 会自动接管底层的系统关闭信号)
    application.run_polling()

if __name__ == '__main__':
    main()