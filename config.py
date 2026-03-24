import yaml
import os
import sys

# 确保配置文件存在
CONFIG_PATH = "config.yaml"
if not os.path.exists(CONFIG_PATH):
    print(f"❌ 找不到配置文件 {CONFIG_PATH}，请先创建它！")
    sys.exit(1)

# 读取 YAML
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

# 导出变量供其他模块使用
BOT_TOKEN = _config['bot']['token']
TARGET_CHAT_ID = _config['bot']['target_chat_id']
ADMIN_IDS = _config['bot']['admin_ids']
BUFFER_LIMIT = _config['database']['buffer_limit']
FLUSH_INTERVAL = _config['database']['flush_interval_seconds']
MAX_TEXT_LENGTH = _config['database']['max_text_length']
DISPLAY_TEXT_LENGTH = _config['database']['display_text_length']
TOPIC_MAPPING = _config['bot'].get('topic_mapping') or {}
DB_DIR = _config['database'].get('db_dir', '.')