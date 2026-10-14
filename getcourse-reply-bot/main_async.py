import asyncio
import time
import traceback
from datetime import UTC, datetime
from typing import Any

import aiohttp
from config import OPENAI_API_KEY
from credential_manager import (
    decrypt_password,
    fetch_active_accounts,
    get_service_supabase_client,
)
from document_parser import (
    ExtractedContent,
    extract_content_from_message_text,
    process_attachment_content,
)
from getcourse_client import (
    AsyncGetCourseClient,
    Thread,
)
from gpt_responder import generate_final_response, generate_gpt_response
from gpt_responder import get_sync_supabase_client as get_gpt_supabase_client
from state_manager import DbStateManager


async def update_verification_status(account_id: str, status: str):
    if not account_id:
        return
    try:
        print(f"StateManager: Обновление статуса верификации акк={account_id} на '{status}'")
        client = get_service_supabase_client()
        now_iso = datetime.now(UTC).isoformat()
        update_data = {"last_verification_status": status, "last_verified_at": now_iso}
        if status == "failed":
            update_data["is_active"] = False
            print(f"StateManager: Деактивация аккаунта {account_id}.")
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: client.table("getcourse_accounts")
            .update(update_data)
            .eq("id", account_id)
            .execute(),
        )
    except Exception as e:
        print(f"StateManager: Ошибка обновления статуса верификации акк={account_id}: {e}")


async def process_single_account(account_data: dict, state_manager: DbStateManager):
    account_id = account_data.get("id")
    user_id = account_data.get("user_id")
    subdomain = account_data.get("subdomain")
    email = account_data.get("email")
    encrypted_password = account_data.get("encrypted_password")
    iv = account_data.get("iv")

    if not all([account_id, user_id, subdomain, email, encrypted_password, iv]):
        print(f"Пропуск аккаунта ID {account_id or '???'}: неполные данные.")
        return

    print(
        f"\n=== Начало обработки аккаунта: {subdomain} (User: {user_id}, AccID: {account_id}) ==="
    )
    await update_processing_status(account_id, True)

    password = decrypt_password(encrypted_password, iv)

    gc_client_instance: AsyncGetCourseClient | None = None
    login_successful_this_time = False

    if not password:
        print(f"[{subdomain}] ОШИБКА: Не удалось расшифровать пароль для аккаунта {account_id}.")
    else:
        gc_client_instance = AsyncGetCourseClient(
            account_id=account_id,
            user_id=user_id,
            subdomain=subdomain,
            email=email,
            password=password,
            state_manager=state_manager,
        )
        try:
            await gc_client_instance._start_driver_async()
            if not gc_client_instance._driver or not gc_client_instance._wait:
                raise ConnectionError(
                    f"[{subdomain}] Не удалось запустить WebDriver для аккаунта {account_id}."
                )
            await gc_client_instance._ensure_logged_in()
            if gc_client_instance.login_failed:
                print(f"[{subdomain}] Вход не удался, дальнейшая обработка невозможна.")
                login_successful_this_time = False
            else:
                print(f"[{subdomain}] Вход успешен. Получение тредов...")
                login_successful_this_time = True
                current_getcourse_cookies = (
                    await gc_client_instance.get_current_session_cookies_for_aiohttp()
                )
                threads_to_process = await gc_client_instance.get_threads_to_process()
                if threads_to_process:
                    print(f"[{subdomain}] Найдено {len(threads_to_process)} тредов для обработки.")
                    processed_in_this_run = set()
                    thread_tasks = [
                        asyncio.create_task(
                            process_single_thread(
                                gc_client_instance,
                                thread,
                                processed_in_this_run,
                                current_getcourse_cookies,
                            )
                        )
                        for thread in threads_to_process
                        if thread.thread_id not in processed_in_this_run
                    ]
                    if thread_tasks:
                        await asyncio.gather(*thread_tasks)
                else:
                    print(f"[{subdomain}] Нет новых тредов для обработки.")
        except ConnectionError as e:
            print(f"[{subdomain}] Ошибка соединения/WebDriver при обработке аккаунта: {e}")
            login_successful_this_time = False
        except Exception as e:
            print(f"[{subdomain}] Непредвиденная ошибка при обработке аккаунта: {e}")
            traceback.print_exc()
            login_successful_this_time = False
        finally:
            if gc_client_instance and gc_client_instance._driver:
                await gc_client_instance._quit_driver()
    await update_processing_status(account_id, False)
    status_to_set = "success" if login_successful_this_time else "failed"
    await update_verification_status(account_id, status_to_set)
    print(f"=== Завершение обработки аккаунта: {subdomain} (ID: {account_id}) ===")


async def process_single_thread(
    client: AsyncGetCourseClient,
    thread: Thread,
    processed_ids: set,
    getcourse_cookies_for_download: dict[str, str] | None,
):
    subdomain = client.subdomain
    user_id = client.user_id
    thread_id = thread.thread_id
    getcourse_account_id = client.account_id

    print(
        f"\n--- [{subdomain}] Обработка треда {thread_id} (User: {user_id}, GCAcc: {getcourse_account_id}) ---"
    )

    print(f"  Thread {thread_id}: Processing {len(thread.messages)} messages.")
    for i, msg in enumerate(thread.messages):
        print(
            f"    Msg {i + 1} (ID: {msg.id}): Author='{msg.author.name}', is_teacher={msg.is_teacher_reply}, Attachments: {len(msg.attachments)}, Text Links: {len(msg.found_text_links)}"
        )
        if msg.attachments:
            for att_idx, att in enumerate(msg.attachments):
                print(
                    f"      Attach {att_idx + 1}: Name='{att.filename}', Type='{att.type}', URL='{att.url}'"
                )

    try:
        text_to_send: str | None = None
        prompt_settings_for_lesson: dict[str, Any] | None = None
        is_final_message_turn = False
        supabase_client_for_db_calls = await get_gpt_supabase_client()

        all_extracted_content_for_thread: list[ExtractedContent] = []

        if thread.messages:
            last_user_message_with_attachments = None
            for msg in reversed(thread.messages):
                if not msg.is_teacher_reply and msg.attachments:
                    last_user_message_with_attachments = msg
                    break

            last_message = thread.messages[-1]

            if last_user_message_with_attachments:
                print(
                    f"  Parser: Анализ {len(last_user_message_with_attachments.attachments)} прикрепленных файлов в сообщении ID {last_user_message_with_attachments.id}..."
                )
                attachment_processing_tasks = []
                pdf_attachments = [
                    att
                    for att in last_user_message_with_attachments.attachments
                    if att.type == "pdf" and att.url
                ]
                if pdf_attachments:
                    http_session = aiohttp.ClientSession()
                    for pdf_att in pdf_attachments:
                        attachment_processing_tasks.append(
                            process_attachment_content(
                                pdf_att, http_session, getcourse_cookies_for_download
                            )
                        )

                docx_attachments = [
                    att
                    for att in last_user_message_with_attachments.attachments
                    if att.type == "docx" and att.url
                ]
                if docx_attachments:
                    if not (client._driver and client._wait):
                        await client._start_driver_async()
                    if client._driver and client._wait:
                        for docx_att in docx_attachments:
                            attachment_processing_tasks.append(
                                client._download_and_parse_docx_with_selenium(
                                    docx_att, client._driver, client._wait
                                )
                            )
                    else:
                        print(
                            "  Parser (DOCX Selenium): WebDriver клиента не активен для скачивания DOCX."
                        )
                        for docx_att in docx_attachments:
                            all_extracted_content_for_thread.append(
                                ExtractedContent(
                                    source_url=docx_att.url,
                                    filename=docx_att.filename,
                                    content_type="docx",
                                    error="WebDriver не был активен для скачивания",
                                )
                            )

                if attachment_processing_tasks:
                    results = await asyncio.gather(
                        *attachment_processing_tasks, return_exceptions=True
                    )
                    for idx, res in enumerate(results):
                        if isinstance(res, ExtractedContent):
                            all_extracted_content_for_thread.append(res)
                            print(
                                f"  Parser (Attachment): Обработано: {res.filename or res.source_url}, Ошибка: {res.error}, Текст: {'Есть' if res.text_content else 'Нет'}"
                            )
                        elif isinstance(res, str):
                            docx_att = None
                            if idx < len(docx_attachments):
                                docx_att = docx_attachments[idx]
                            if res.startswith("[Ошибка"):
                                print(
                                    f"  Parser (Attachment): Получена строка ошибки от парсера: {res}"
                                )
                                if docx_att:
                                    all_extracted_content_for_thread.append(
                                        ExtractedContent(
                                            source_url=docx_att.url,
                                            filename=docx_att.filename,
                                            content_type="docx",
                                            error=res,
                                        )
                                    )
                            else:
                                if docx_att:
                                    all_extracted_content_for_thread.append(
                                        ExtractedContent(
                                            source_url=docx_att.url,
                                            filename=docx_att.filename,
                                            content_type="docx",
                                            text_content=res,
                                        )
                                    )
                        elif isinstance(res, Exception):
                            print(
                                f"  Parser (Attachment): Исключение при обработке: {type(res).__name__} - {res}"
                            )
                    if pdf_attachments:
                        await http_session.close()

            if last_message.text:
                print(
                    f"  Parser: Анализ текста сообщения ID {last_message.id} на ссылки (Google Docs/Sheets)..."
                )
                try:
                    extracted_from_text = await extract_content_from_message_text(last_message.text)
                    if extracted_from_text:
                        added_from_text_count = 0
                        for extracted_item in extracted_from_text:
                            if not any(
                                existing.source_url == extracted_item.source_url
                                for existing in all_extracted_content_for_thread
                            ):
                                all_extracted_content_for_thread.append(extracted_item)
                                print(
                                    f"  Parser (TextLink): Извлечено: {extracted_item.filename or extracted_item.source_url}, Ошибка: {extracted_item.error}"
                                )
                                added_from_text_count += 1
                        if added_from_text_count > 0:
                            print(
                                f"  Parser (TextLink): Дополнительно извлечено {added_from_text_count} блоков из текста."
                            )
                except Exception as e_extract:
                    print(
                        f"  Parser (TextLink): Ошибка при вызове extract_content_from_message_text: {e_extract}"
                    )
                    traceback.print_exc()

        if all_extracted_content_for_thread:
            print(
                f"  Parser: Всего извлечено {len(all_extracted_content_for_thread)} блоков контента для GPT."
            )

        if not OPENAI_API_KEY:
            text_to_send = f"[ЗАГЛУШКА {subdomain}] Нет OpenAI Key для треда {thread_id}"
            print(f"[{subdomain}] OpenAI Key отсутствует, используется заглушка.")
        else:
            (
                regular_response_text,
                prompt_settings_for_lesson,
            ) = await generate_gpt_response(
                thread,
                user_id=user_id,
                getcourse_account_id=getcourse_account_id,
                extracted_docs_content=all_extracted_content_for_thread,
            )
            text_to_send = regular_response_text

        if prompt_settings_for_lesson:
            current_message_count = len(thread.messages)
            max_messages = prompt_settings_for_lesson.get("max_messages_in_thread")
            if (
                max_messages is not None
                and max_messages > 0
                and current_message_count >= max_messages
            ):
                print(
                    f"[{subdomain}] Тред {thread_id} достиг лимита ({current_message_count}/{max_messages}). Генерация финального сообщения."
                )
                is_final_message_turn = True
                final_settings_for_generation = {
                    "final_message_text": prompt_settings_for_lesson.get("final_message_text"),
                    "use_final_message_prompt_id": prompt_settings_for_lesson.get(
                        "use_final_message_prompt_id"
                    ),
                }
                final_response = await generate_final_response(
                    thread,
                    user_id,
                    getcourse_account_id,
                    final_settings_for_generation,
                    supabase_client_for_db_calls,
                    extracted_docs_content=all_extracted_content_for_thread,
                )
                text_to_send = final_response if final_response else "Обсуждение завершено."
            elif text_to_send is None and OPENAI_API_KEY:
                print(
                    f"[{subdomain}] [ОШИБКА] GPT не сгенерировал обычный ответ для {thread_id} (лимит не проверялся)."
                )
                return
        elif text_to_send is None and OPENAI_API_KEY:
            print(
                f"[{subdomain}] [ОШИБКА] Не удалось загрузить настройки промпта для {thread_id} (лимит не проверен)."
            )
            return

        if not text_to_send or not text_to_send.strip():
            print(f"[{subdomain}] Нет текста для отправки в тред {thread_id}. Пропуск.")
            return

        print(
            f"[{subdomain}] {'Финальный о' if is_final_message_turn else 'О'}твет для {thread_id}:\n{text_to_send}\n-----------------"
        )
        success = await client.process_thread_response(thread, text_to_send)

        if success:
            print(f"[{subdomain}] Ответ на тред {thread_id} успешно отправлен.")
            processed_ids.add(thread_id)
            if is_final_message_turn:
                print(f"[{subdomain}] Тред {thread_id} обработан с финальным сообщением.")
        else:
            print(
                f"[{subdomain}] [ОШИБКА] Не удалось отправить {'финальный ' if is_final_message_turn else ''}ответ на тред {thread_id}."
            )

    except Exception as e:
        print(f"[{subdomain}] КРИТИЧЕСКАЯ ОШИБКА при обработке треда {thread_id}: {e}")
        traceback.print_exc()
    finally:
        await asyncio.sleep(0.5)


async def run_bot_cycle(check_interval_seconds: int = 60):
    print(f"Запуск цикла проверки каждые {check_interval_seconds} секунд...")
    try:
        state_manager = DbStateManager()
    except ValueError as e:
        print(f"Не удалось создать StateManager: {e}. Выход.")
        return
    try:
        _ = get_service_supabase_client()
        _ = await get_gpt_supabase_client()
    except ValueError as e:
        print(f"Ошибка Supabase клиента: {e}. Выход.")
        return

    while True:
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] --- Начало цикла проверки ---")
        active_accounts = []
        try:
            active_accounts = await fetch_active_accounts()
        except Exception as e:
            print(f"Критическая ошибка получения аккаунтов: {e}")

        if not active_accounts:
            print("Активные аккаунты не найдены. Пауза.")
        else:
            print(f"Найдено {len(active_accounts)} активных аккаунтов для обработки.")
            account_tasks = [
                asyncio.create_task(process_single_account(acc, state_manager))
                for acc in active_accounts
            ]
            if account_tasks:
                await asyncio.gather(*account_tasks)

        print(f"--- Цикл проверки завершен. Пауза {check_interval_seconds} секунд ---")
        await asyncio.sleep(check_interval_seconds)


async def update_processing_status(account_id: str, is_processing: bool):
    if not account_id:
        return
    try:
        status_text = "processing" if is_processing else "idle"
        print(f"StateManager: Установка статуса '{status_text}' для акк={account_id}")
        client = get_service_supabase_client()
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: client.table("getcourse_accounts")
            .update({"is_currently_processing": is_processing})
            .eq("id", account_id)
            .execute(),
        )
    except Exception as e:
        print(f"StateManager: Ошибка обновления processing_status для акк={account_id}: {e}")


async def main():
    await run_bot_cycle(check_interval_seconds=30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nЗавершение работы...")
    except Exception as global_e:
        print(f"\nГлобальная ошибка: {global_e}")
        traceback.print_exc()
