import os

from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

WAIT_TIMEOUT = 15
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

if not OPENAI_API_KEY:
    print("Предупреждение: OPENAI_API_KEY не найден.")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("Критическая ошибка: SUPABASE_URL или SUPABASE_ANON_KEY не найдены.")
if not SUPABASE_SERVICE_KEY:
    print("Предупреждение: SUPABASE_SERVICE_KEY не найден.")
if not ENCRYPTION_KEY:
    print("Критическая ошибка: ENCRYPTION_KEY не найден.")
    raise ValueError("ENCRYPTION_KEY не установлен")
elif len(ENCRYPTION_KEY.encode()) not in [16, 24, 32]:
    print(
        f"Критическая ошибка: Неверная длина ENCRYPTION_KEY ({len(ENCRYPTION_KEY.encode())} байт)."
    )
    raise ValueError("Неверная длина ENCRYPTION_KEY")
