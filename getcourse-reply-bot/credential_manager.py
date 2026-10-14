import asyncio

from config import ENCRYPTION_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from supabase import Client, create_client

_supabase_service_client: Client | None = None


def get_service_supabase_client() -> Client:
    global _supabase_service_client
    if _supabase_service_client is None:
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            try:
                _supabase_service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                print("CredentialManager: Supabase service client инициализирован.")
            except Exception as e:
                print(
                    f"CredentialManager: Критическая ошибка инициализации Supabase service client: {e}"
                )
                raise ValueError("Не удалось инициализировать Supabase service client.") from e
        else:
            raise ValueError("SUPABASE_URL и SUPABASE_SERVICE_KEY должны быть установлены.")
    return _supabase_service_client


async def _run_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    client = get_service_supabase_client()
    return await loop.run_in_executor(None, lambda: func(client, *args, **kwargs))


async def fetch_active_accounts() -> list[dict]:
    print("CredentialManager: Запрос активных аккаунтов GetCourse...")
    all_accounts = []
    try:

        def sync_call(client: Client):
            return client.table("getcourse_accounts").select("*").eq("is_active", True).execute()

        response = await _run_sync(sync_call)

        if response.data:
            all_accounts = response.data
            print(f"CredentialManager: Найдено {len(all_accounts)} активных аккаунтов.")
        else:
            if hasattr(response, "error") and response.error:
                print(
                    f"CredentialManager: Ошибка Supabase при получении аккаунтов: {response.error}"
                )
            else:
                print("CredentialManager: Активные аккаунты GetCourse не найдены.")
    except Exception as e:
        print(f"CredentialManager: Ошибка при получении аккаунтов из Supabase: {e}")
    return all_accounts


def decrypt_password(encrypted_password_hex: str, iv_hex: str) -> str | None:
    if not encrypted_password_hex or not iv_hex:
        print("Ошибка дешифрования: Отсутствует пароль или IV.")
        return None
    try:
        key = ENCRYPTION_KEY.encode()
        iv = bytes.fromhex(iv_hex)
        ciphertext = bytes.fromhex(encrypted_password_hex)

        if len(iv) != 16:
            raise ValueError(f"Неверная длина IV ({len(iv)} байт), ожидалось 16.")

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext_bytes = unpadder.update(padded_plaintext) + unpadder.finalize()

        return plaintext_bytes.decode("utf-8")
    except ValueError as ve:
        print(f"Ошибка ValueError при дешифровании: {ve}")
        return None
    except Exception as e:
        print(f"Неожиданная ошибка дешифрования пароля: {e}")
        return None
