import asyncio
import re
import traceback
from typing import Any

from config import OPENAI_API_KEY, SUPABASE_ANON_KEY, SUPABASE_URL
from getcourse_client import ExtractedContent, Thread
from openai import AsyncOpenAI, OpenAIError
from supabase import Client, create_client

aclient: AsyncOpenAI | None = None
if OPENAI_API_KEY:
    try:
        aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)
        print("GPT Responder: Асинхронный OpenAI клиент инициализирован.")
    except Exception as e:
        print(f"GPT Responder: Ошибка инициализации AsyncOpenAI: {e}")
else:
    print("GPT Responder: Warn: AsyncOpenAI client не инициализирован.")

_supabase_sync: Client | None = None
_supabase_init_lock = asyncio.Lock()


async def get_sync_supabase_client() -> Client:
    global _supabase_sync
    if _supabase_sync is None:
        async with _supabase_init_lock:
            if _supabase_sync is None:
                if not SUPABASE_URL or not SUPABASE_ANON_KEY:
                    raise ValueError(
                        "SUPABASE_URL и SUPABASE_ANON_KEY должны быть установлены для GPT Supabase client."
                    )
                try:
                    print("GPT Responder: Initializing sync Supabase client (anon key)...")
                    temp_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: temp_client.table("prompts").select("lesson_id").limit(1).execute(),
                    )
                    _supabase_sync = temp_client
                    print("GPT Responder: Sync Supabase client initialized.")
                except Exception as e:
                    _supabase_sync = None
                    print(f"GPT Responder: Critical error initializing Sync Supabase: {e}")
                    raise ValueError("Failed to initialize Sync Supabase client for GPT") from e
    if _supabase_sync is None:
        raise ConnectionError(
            "Supabase client for GPT responder is None after initialization attempt."
        )
    return _supabase_sync


def _sync_fetch_prompt(
    supabase_client: Client, user_id: str, lesson_id_or_prompt_id: str
) -> dict[str, Any] | None:
    print(
        f"  GPT: _sync_fetch_prompt - Запрос деталей промпта для user={user_id}, id/lesson_id={lesson_id_or_prompt_id}"
    )
    if not user_id or not lesson_id_or_prompt_id:
        print("  GPT Warn: _sync_fetch_prompt - Недостаточно ID.")
        return None

    try:
        is_fetching_by_prompt_id = (
            "-" in lesson_id_or_prompt_id and len(lesson_id_or_prompt_id) == 36
        )

        query = (
            supabase_client.table("prompts")
            .select(
                "id, lesson_id, prompt_text, max_messages_in_thread, final_message_text, use_final_message_prompt_id"
            )
            .eq("user_id", user_id)
        )

        if is_fetching_by_prompt_id:
            query = query.eq("id", lesson_id_or_prompt_id)
            print(f"  GPT: _sync_fetch_prompt - Поиск по prompt_id: {lesson_id_or_prompt_id}")
        else:
            query = query.eq("lesson_id", lesson_id_or_prompt_id)
            print(f"  GPT: _sync_fetch_prompt - Поиск по lesson_id: {lesson_id_or_prompt_id}")

        response = query.limit(1).maybe_single().execute()

        if response.data:
            return response.data
        else:
            if not is_fetching_by_prompt_id and lesson_id_or_prompt_id != "default":
                print(
                    f"  GPT: _sync_fetch_prompt - Промпт для lesson_id={lesson_id_or_prompt_id} не найден. Ищем default..."
                )
                default_response = (
                    supabase_client.table("prompts")
                    .select(
                        "id, lesson_id, prompt_text, max_messages_in_thread, final_message_text, use_final_message_prompt_id"
                    )
                    .eq("user_id", user_id)
                    .eq("lesson_id", "default")
                    .limit(1)
                    .maybe_single()
                    .execute()
                )
                if default_response.data:
                    print(
                        f"  GPT: _sync_fetch_prompt - Default промпт найден: {default_response.data}"
                    )
                    return default_response.data
                else:
                    print("  GPT: _sync_fetch_prompt - Default промпт тоже не найден.")
            else:
                print(
                    f"  GPT: _sync_fetch_prompt - Промпт не найден (для id/lesson_id: {lesson_id_or_prompt_id})."
                )
            return None

    except Exception as e:
        print(f"  GPT: _sync_fetch_prompt - Ошибка при получении промпта: {e}")
        return None


def format_thread_history(thread: Thread) -> str:
    history_lines = []
    for msg in thread.messages:
        role = "Преподаватель" if msg.is_teacher_reply else "Студент"
        author_info = msg.author.name or (f"ID {msg.author.id}" if msg.author.id else "Неизвестный")
        time_info = (
            msg.timestamp.precise_time_str or msg.timestamp.relative_time_str or "Неизв. время"
        )
        text = msg.text or "[Нет текста]"
        history_lines.append(f"[{time_info}] {role} ({author_info}): {text}")
    return "\n".join(history_lines)


def _sync_fetch_course_variables(
    client: Client, user_id: str, getcourse_account_id: str
) -> dict[str, str]:
    variables_dict: dict[str, str] = {}
    if not user_id or not getcourse_account_id:
        print(
            f"  GPT: Недостаточно данных для загрузки переменных курса (user_id: {user_id}, gc_acc_id: {getcourse_account_id})"
        )
        return variables_dict
    try:
        print(
            f"  GPT: Загрузка переменных курса для user={user_id}, gc_account_id={getcourse_account_id}..."
        )
        query = (
            client.table("course_variables")
            .select("variable_name, variable_value")
            .eq("user_id", user_id)
            .eq("getcourse_account_id", getcourse_account_id)
        )
        response = query.execute()
        print(
            f"  GPT: _sync_fetch_course_variables - Ответ от Supabase: status={getattr(response, 'status_code', 'N/A')}, count={getattr(response, 'count', 'N/A')}"
        )
        if hasattr(response, "data") and response.data is not None:
            print(f"  GPT: _sync_fetch_course_variables - Сырые данные: {response.data}")
        else:
            print("  GPT: _sync_fetch_course_variables - Сырые данные: отсутствуют или None")
        if response.data:
            for item in response.data:
                if item.get("variable_name") and item.get("variable_value") is not None:
                    variables_dict[item["variable_name"]] = item["variable_value"]
                else:
                    print(
                        f"  GPT: _sync_fetch_course_variables - Пропущен элемент из-за отсутствия variable_name или variable_value: {item}"
                    )
            print(f"  GPT: Загружено {len(variables_dict)} переменных курса.")
        else:
            error_info = getattr(response, "error", None)
            if error_info:
                print(f"  GPT: Ошибка Supabase при загрузке переменных: {error_info}")
            else:
                print(
                    f"  GPT: Переменные курса не найдены для user={user_id}, gc_account_id={getcourse_account_id}"
                )
    except Exception as e:
        print(
            f"  GPT: _sync_fetch_course_variables - Неожиданная ошибка при загрузке переменных курса: {type(e).__name__} - {e}"
        )
        traceback.print_exc()
    return variables_dict


def substitute_variables_in_prompt(template: str, variables: dict[str, str]) -> str:
    if not variables:
        return template

    def replace_match(match: re.Match) -> str:
        variable_name = match.group(1).strip()
        return variables.get(variable_name, f"{{{{UNDEFINED_VARIABLE: {variable_name}}}}}")

    processed_template = re.sub(r"\{\{\s*([\w_]+)\s*\}\}", replace_match, template)
    return processed_template


async def generate_final_response(
    thread_context: Thread,
    user_id: str,
    getcourse_account_id: str,
    final_message_settings: dict[str, Any],
    supabase_client: Client,
    model: str = "gpt-4o-mini",
) -> str | None:
    if not aclient:
        print("  GPT FinalResponse: OpenAI client не настроен.")
        return "[Ошибка OpenAI: Клиент не настроен]"

    static_final_text = final_message_settings.get("final_message_text")
    final_prompt_record_id = final_message_settings.get("use_final_message_prompt_id")

    if static_final_text:
        print(
            f"  GPT FinalResponse: Используется статический финальный текст: '{static_final_text}'"
        )
        return static_final_text

    if final_prompt_record_id:
        print(f"  GPT FinalResponse: Генерация по финальному промпту ID: {final_prompt_record_id}")
        loop = asyncio.get_running_loop()

        final_prompt_info = await loop.run_in_executor(
            None, _sync_fetch_prompt, supabase_client, user_id, final_prompt_record_id
        )

        if final_prompt_info and final_prompt_info.get("prompt_text"):
            final_prompt_template = final_prompt_info["prompt_text"]

            course_variables = await loop.run_in_executor(
                None,
                _sync_fetch_course_variables,
                supabase_client,
                user_id,
                getcourse_account_id,
            )
            if course_variables:
                final_prompt_template = substitute_variables_in_prompt(
                    final_prompt_template, course_variables
                )

            history_for_final = "Диалог достиг своего лимита."
            if thread_context.messages and "{history}" in final_prompt_template.lower():
                last_student_message = next(
                    (msg for msg in reversed(thread_context.messages) if not msg.is_teacher_reply),
                    None,
                )
                if last_student_message:
                    history_for_final = f"Студент ({last_student_message.author.name or 'Студент'}) написал: {last_student_message.text or '[Нет текста]'}"

            final_api_prompt: str
            if "{history}" not in final_prompt_template.lower():
                final_api_prompt = f"{final_prompt_template}\n\nКонтекст завершения:\n{history_for_final}\n\nТвой финальный ответ, завершающий диалог:"
            else:
                try:
                    final_api_prompt = final_prompt_template.format(history=history_for_final)
                except Exception as fe:
                    print(
                        f"  GPT FinalResponse Error: Ошибка форматирования финального промпта: {fe}."
                    )
                    final_api_prompt = f"{final_prompt_template}\n\n{history_for_final}\n\nОтвет:"

            try:
                print(
                    f"  GPT FinalResponse: Запрос к OpenAI (model: {model}). Промпт: {final_api_prompt[:100]}..."
                )
                response = await aclient.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": final_api_prompt}],
                    temperature=0.5,
                    max_tokens=150,
                )
                ai_response = response.choices[0].message.content
                if ai_response:
                    print("  GPT FinalResponse: Финальный ответ от OpenAI получен.")
                    return ai_response.strip()
                else:
                    print("  GPT FinalResponse Warn: OpenAI вернул пустой финальный ответ.")
                    return "[Финальное сообщение не сгенерировано]"
            except OpenAIError as e:
                print(f"  GPT FinalResponse Error: OpenAI API Error: {e}")
                return f"[Ошибка OpenAI API (финал): {e}]"
            except Exception as e:
                print(f"  GPT FinalResponse Error: Unexpected OpenAI call error: {e}")
                return "[Ошибка OpenAI (финал)]"
        else:
            print(
                f"  GPT FinalResponse Warn: Финальный промпт ID {final_prompt_record_id} не найден или пуст."
            )

    return "Обсуждение этого вопроса завершено. Если у вас есть другие вопросы, вы можете задать их снова."


async def generate_gpt_response(
    thread: Thread,
    user_id: str,
    getcourse_account_id: str,
    extracted_docs_content: list[ExtractedContent] | None = None,
    model: str = "gpt-4o-mini",
) -> tuple[str | None, dict[str, Any] | None]:
    prompt_settings_for_lesson: dict[str, Any] | None = None

    if not aclient:
        print("  GPT Error: AsyncOpenAI client не настроен.")
        return "[Ошибка OpenAI: Клиент не инициализирован]", prompt_settings_for_lesson
    if not user_id or not getcourse_account_id:
        print(
            "  GPT Error: Недостаточно ID для генерации ответа (user_id или getcourse_account_id)."
        )
        return (
            "[Ошибка: Не предоставлены все необходимые ID]",
            prompt_settings_for_lesson,
        )

    print(
        f"  GPT: Генерация ответа тред={thread.thread_id}, user={user_id}, gc_acc={getcourse_account_id}..."
    )

    supabase_client_for_sync: Client | None = None
    try:
        supabase_client_for_sync = await get_sync_supabase_client()
    except Exception as e:
        print(f"  GPT Error: Не удалось получить Supabase клиент: {e}")
        return (
            f"[Ошибка конфигурации: Supabase клиент ({type(e).__name__})]",
            prompt_settings_for_lesson,
        )

    loop = asyncio.get_running_loop()

    try:
        prompt_settings_for_lesson = await loop.run_in_executor(
            None,
            _sync_fetch_prompt,
            supabase_client_for_sync,
            user_id,
            thread.lesson.id,
        )
    except Exception as e:
        print(f"  GPT Error: Ошибка вызова _sync_fetch_prompt: {e}")
        return "[Ошибка: Не удалось загрузить промпт урока]", prompt_settings_for_lesson

    if not prompt_settings_for_lesson or not prompt_settings_for_lesson.get("prompt_text"):
        print(
            f"  GPT Error: Промпт урока не найден или текст промпта пуст для user={user_id}, lesson={thread.lesson.id}."
        )
        return "[Ошибка: Промпт урока не найден или пуст]", prompt_settings_for_lesson

    prompt_template = prompt_settings_for_lesson["prompt_text"]

    course_variables: dict[str, str] = {}
    try:
        course_variables = await loop.run_in_executor(
            None,
            _sync_fetch_course_variables,
            supabase_client_for_sync,
            user_id,
            getcourse_account_id,
        )
    except Exception as e:
        print(f"  GPT Warn: Ошибка вызова _sync_fetch_course_variables: {e}")

    if course_variables:
        print(f"  GPT: Применение {len(course_variables)} переменных курса к промпту...")
        prompt_template = substitute_variables_in_prompt(prompt_template, course_variables)
    else:
        print(
            "  GPT: Переменные курса не найдены или не загружены, используется оригинальный текст промпта урока."
        )

    history = format_thread_history(thread)

    additional_context_from_docs = ""
    if extracted_docs_content:
        print(
            f"  GPT: Добавление {len(extracted_docs_content)} блоков извлеченного контента в промпт..."
        )
        for idx, doc_content in enumerate(extracted_docs_content):
            additional_context_from_docs += f"\n\n--- Начало документа {idx + 1} ({doc_content.filename or doc_content.source_url}) ---\n"
            if doc_content.text_content:
                max_doc_text_len = 2000
                text_to_add = doc_content.text_content[:max_doc_text_len]
                if len(doc_content.text_content) > max_doc_text_len:
                    text_to_add += "\n[...текст документа сокращен...]"
                additional_context_from_docs += text_to_add
            elif doc_content.error:
                additional_context_from_docs += f"[Ошибка обработки документа: {doc_content.error}]"
            else:
                additional_context_from_docs += "[Контент не извлечен или пуст]"
            additional_context_from_docs += f"\n--- Конец документа {idx + 1} ---\n"

    final_prompt_for_openai: str

    if "{history}" not in prompt_template.lower():
        final_prompt_for_openai = f"{prompt_template}\n{additional_context_from_docs}\n\nИстория переписки:\n{history}\n\nТвой ответ:"
    else:
        if "{{document_context}}" in prompt_template.lower():
            prompt_template_with_docs = prompt_template.replace(
                "{{document_context}}", additional_context_from_docs
            )
        else:
            prompt_template_with_docs = f"{prompt_template}\n{additional_context_from_docs}"
        try:
            final_prompt_for_openai = prompt_template.format(history=history)
        except KeyError as ke:
            print(
                f"  GPT Error: Ошибка форматирования промпта (KeyError: {ke}). Возможно, плейсхолдер {ke} не был заменен переменной курса. Используем добавление истории в конец."
            )
            final_prompt_for_openai = (
                f"{prompt_template}\n\nИстория переписки:\n{history}\n\nТвой ответ:"
            )
        except Exception as fe:
            print(
                f"  GPT Error: Непредвиденная ошибка форматирования промпта: {fe}. Используем добавление истории в конец."
            )
            final_prompt_for_openai = (
                f"{prompt_template_with_docs}\n\nИстория:\n{history}\n\nОтвет:"
            )

    print(
        f"  GPT DEBUG: Финальный промпт для OpenAI:\n{final_prompt_for_openai}\n--------------------"
    )

    try:
        print(f"  GPT: Отправка запроса к OpenAI (model: {model})...")
        response = await aclient.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": final_prompt_for_openai}],
            temperature=0.7,
            max_tokens=500,
        )
        ai_response_content = response.choices[0].message.content
        if ai_response_content:
            print("  GPT: Ответ от OpenAI получен.")
            return ai_response_content.strip(), prompt_settings_for_lesson
        else:
            print("  GPT Warn: OpenAI вернул пустой ответ.")
            return "[AI: Пустой ответ]", prompt_settings_for_lesson
    except OpenAIError as e:
        print(f"  GPT Error: OpenAI API Error: {e}")
        return f"[Ошибка OpenAI API: {e}]", prompt_settings_for_lesson
    except Exception as e:
        print(f"  GPT Error: Unexpected OpenAI call error: {type(e).__name__} - {e}")
        return (
            f"[Непредвиденная ошибка OpenAI: {type(e).__name__}]",
            prompt_settings_for_lesson,
        )
