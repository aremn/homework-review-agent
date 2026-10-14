import asyncio
from datetime import UTC, datetime

from config import SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL
from supabase import Client, create_client


class DbStateManager:
    def __init__(self):
        self._supabase_client: Client | None = None
        use_service_key = False
        client_url = SUPABASE_URL
        client_key = SUPABASE_SERVICE_KEY if use_service_key else SUPABASE_ANON_KEY

        if client_url and client_key:
            try:
                self._supabase_client = create_client(client_url, client_key)
                key_type = "service" if use_service_key else "anon"
                print(f"StateManager: Supabase {key_type} клиент инициализирован.")
            except Exception as e:
                print(f"StateManager: Критическая ошибка инициализации Supabase: {e}")
                raise ValueError("Не удалось инициализировать Supabase для StateManager.") from e
        else:
            key_name = "SUPABASE_SERVICE_KEY" if use_service_key else "SUPABASE_ANON_KEY"
            raise ValueError(f"SUPABASE_URL и {key_name} должны быть установлены.")

    async def _run_sync(self, func, *args, **kwargs):
        if not self._supabase_client:
            raise ConnectionError("Supabase client не инициализирован в StateManager")
        loop = asyncio.get_running_loop()
        client = self._supabase_client
        return await loop.run_in_executor(None, lambda: func(client, *args, **kwargs))

    async def get_last_message_id(self, subdomain: str, thread_id: str) -> str | None:
        try:

            def sync_call(client: Client, sub: str, thr_id: str):
                return (
                    client.table("bot_processing_state")
                    .select("last_message_id")
                    .eq("subdomain", sub)
                    .eq("thread_id", thr_id)
                    .limit(1)
                    .maybe_single()
                    .execute()
                )

            response = await self._run_sync(sync_call, subdomain, thread_id)
            if response.data:
                return response.data.get("last_message_id")
            return None
        except Exception as e:
            print(f"StateManager: Ошибка при получении состояния для {subdomain}/{thread_id}: {e}")
            return None

    async def update_last_message_id(self, subdomain: str, thread_id: str, message_id: str):
        print(f"StateManager: Обновление last_message_id={message_id} для {subdomain}/{thread_id}")
        try:

            def sync_call(client: Client, sub: str, thr_id: str, msg_id: str):
                now_iso = datetime.now(UTC).isoformat()
                return (
                    client.table("bot_processing_state")
                    .upsert(
                        {
                            "subdomain": sub,
                            "thread_id": thr_id,
                            "last_message_id": msg_id,
                            "updated_at": now_iso,
                        },
                        on_conflict="subdomain, thread_id",
                    )
                    .execute()
                )

            response = await self._run_sync(sync_call, subdomain, thread_id, message_id)

            if hasattr(response, "error") and response.error:
                error_msg = (
                    response.error["message"]
                    if isinstance(response.error, dict)
                    else str(response.error)
                )
                raise Exception(f"Supabase error: {error_msg}")
            elif hasattr(response, "data") and response.data:
                pass

        except Exception as e:
            print(f"StateManager: Ошибка при обновлении состояния для {subdomain}/{thread_id}: {e}")
