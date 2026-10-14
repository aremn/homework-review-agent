import asyncio
import io
import os
import re
from urllib.parse import urlparse

import aiohttp
import pypdf
from getcourse_client import Attachment, ExtractedContent

PDF_MAX_SIZE_MB = 5
PDF_MAX_PAGES_TO_PARSE = 10
REQUEST_TIMEOUT_SECONDS = 20


def is_pdf_url(url: str) -> bool:
    parsed_url = urlparse(url.lower())
    return parsed_url.path.endswith(".pdf")


def is_google_doc_url(url: str) -> bool:
    return "docs.google.com/document/d/" in url.lower()


def is_google_sheet_url(url: str) -> bool:
    return "docs.google.com/spreadsheets/d/" in url.lower()


def _parse_pdf_bytes_sync(pdf_bytes: bytes, max_pages: int = PDF_MAX_PAGES_TO_PARSE) -> str | None:
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = pypdf.PdfReader(pdf_file)
        num_pages = len(reader.pages)
        text_parts: list[str] = []
        pages_to_read = min(num_pages, max_pages)

        print(f"  PDF Parser (sync): Чтение {pages_to_read} из {num_pages} страниц PDF...")
        for i in range(pages_to_read):
            page = reader.pages[i]
            extracted = page.extract_text()
            if extracted:
                text_parts.append(extracted)

        extracted_text = "\n\n".join(text_parts).strip()
        if num_pages > max_pages:
            extracted_text += (
                f"\n\n[Примечание: Показан текст только первых {max_pages} страниц из {num_pages}]"
            )
        return extracted_text if extracted_text else None
    except Exception as e:
        print(f"  PDF Parser (sync): Ошибка извлечения текста из PDF: {e}")
        return f"Ошибка обработки PDF: {type(e).__name__}"


async def process_url_content(url: str, session: aiohttp.ClientSession) -> ExtractedContent | None:
    print(f"  Content Processor: Асинхронная обработка URL: {url[:100]}...")
    filename = os.path.basename(urlparse(url).path) or "document"

    if is_pdf_url(url):
        print(f"  Content Processor: Обнаружен PDF: {filename}")
        try:
            async with session.get(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                response.raise_for_status()

                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > PDF_MAX_SIZE_MB * 1024 * 1024:
                    return ExtractedContent(
                        source_url=url,
                        filename=filename,
                        content_type="pdf",
                        error=f"PDF файл слишком большой (>{PDF_MAX_SIZE_MB}MB).",
                    )

                pdf_content_bytes = await response.read()

                if len(pdf_content_bytes) > PDF_MAX_SIZE_MB * 1024 * 1024:
                    return ExtractedContent(
                        source_url=url,
                        filename=filename,
                        content_type="pdf",
                        error=f"Скачанный PDF файл слишком большой (>{PDF_MAX_SIZE_MB}MB).",
                    )

                loop = asyncio.get_running_loop()
                text_content = await loop.run_in_executor(
                    None, _parse_pdf_bytes_sync, pdf_content_bytes
                )

                if text_content and not text_content.startswith("Ошибка"):
                    return ExtractedContent(
                        source_url=url,
                        filename=filename,
                        content_type="pdf",
                        text_content=text_content,
                    )
                else:
                    error_msg = text_content if text_content else "Не удалось извлечь текст из PDF."
                    return ExtractedContent(
                        source_url=url,
                        filename=filename,
                        content_type="pdf",
                        error=error_msg,
                    )

        except aiohttp.ClientError as e:
            print(f"  Content Processor: Ошибка скачивания PDF (aiohttp) {url}: {e}")
            return ExtractedContent(
                source_url=url,
                filename=filename,
                content_type="pdf",
                error=f"Ошибка скачивания PDF: {type(e).__name__}",
            )
        except TimeoutError:
            print(f"  Content Processor: Таймаут при скачивании PDF {url}")
            return ExtractedContent(
                source_url=url,
                filename=filename,
                content_type="pdf",
                error="Таймаут скачивания PDF.",
            )
        except Exception as e:
            print(
                f"  Content Processor: Общая ошибка обработки PDF {url}: {type(e).__name__} - {e}"
            )
            return ExtractedContent(
                source_url=url,
                filename=filename,
                content_type="pdf",
                error=f"Общая ошибка PDF: {type(e).__name__}",
            )

    elif is_google_doc_url(url) or is_google_sheet_url(url):
        content_type = "google_doc" if is_google_doc_url(url) else "google_sheet"
        doc_name = "Google Doc" if content_type == "google_doc" else "Google Sheet"
        print(f"  Content Processor: Обнаружен {doc_name}: {url[:70]}...")

        return ExtractedContent(
            source_url=url,
            filename=doc_name,
            content_type=content_type,
            error=f"Обработка {doc_name} еще не реализована асинхронно.",
        )

    print(f"  Content Processor: URL не распознан или не поддерживается: {url}")
    return None


async def extract_content_from_message_text(
    message_text: str | None,
) -> list[ExtractedContent]:
    if not message_text:
        return []
    url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+', re.IGNORECASE)
    found_urls = list(set(url_pattern.findall(message_text)))
    extracted_contents: list[ExtractedContent] = []
    if found_urls:
        print(f"  Content Extractor: Найдено URL в сообщении: {found_urls}")
        async with aiohttp.ClientSession() as session:
            tasks = [process_url_content(url, session) for url in found_urls]
            results = await asyncio.gather(*tasks)
            for result in results:
                if isinstance(result, ExtractedContent):
                    extracted_contents.append(result)
    return extracted_contents


async def process_google_url_content(
    google_url: str, user_id_for_oauth: str, getcourse_account_id_for_oauth: str
) -> ExtractedContent | None:
    print(
        f"  Google Parser: Обработка Google URL: {google_url[:100]} для пользователя {user_id_for_oauth}"
    )
    filename = "Google Document"
    content_type = "unknown_google_type"

    if is_google_doc_url(google_url):
        content_type = "google_doc"
        filename = "Google Doc"
    elif is_google_sheet_url(google_url):
        content_type = "google_sheet"
        filename = "Google Sheet"

    await asyncio.sleep(0.1)
    return ExtractedContent(
        source_url=google_url,
        filename=filename,
        content_type=content_type,
        text_content=None,
        error="Обработка Google документов через API еще не реализована.",
    )


async def process_attachment_content(
    attachment: Attachment,
    session: aiohttp.ClientSession,
    getcourse_session_cookies: dict[str, str] | None = None,
) -> ExtractedContent:
    url = attachment.url
    filename = attachment.filename or os.path.basename(urlparse(url).path) or "document"
    content_type_from_attachment = attachment.type

    print(
        f"  DocParser: Обработка вложения '{filename}' (тип: {content_type_from_attachment}) с URL: {url[:100]}..."
    )

    if content_type_from_attachment != "pdf":
        print(
            f"  DocParser: Тип вложения '{content_type_from_attachment}' для '{filename}' не PDF. Пропуск."
        )
        return ExtractedContent(
            source_url=url,
            filename=filename,
            content_type=content_type_from_attachment,
            error="Тип файла не PDF, обработка пропущена",
        )

    try:
        request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        cookies_to_send = getcourse_session_cookies if getcourse_session_cookies else None

        print(
            f"  DocParser: Запрос к URL: {url} с заголовками: {request_headers.get('User-Agent')}"
        )
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS + 5),
            allow_redirects=True,
            headers=request_headers,
            cookies=cookies_to_send,
        ) as response:
            print(f"  DocParser: HTTP Статус для '{filename}': {response.status}")

            response_content_type = response.headers.get("Content-Type", "").lower()
            if not response.ok:
                error_text_response = await response.text()
                print(
                    f"  DocParser: Ошибка от сервера {response.status}: {error_text_response[:500]}"
                )
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf",
                    error=f"Ошибка скачивания {response.status}",
                )

            if not (
                "application/pdf" in response_content_type
                or "octet-stream" in response_content_type
                or filename.lower().endswith(".pdf")
            ):
                print(
                    f"  DocParser: Ожидался PDF, но Content-Type: {response_content_type} для {filename}"
                )
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf_unexpected_type",
                    error=f"Неожиданный тип контента: {response_content_type}",
                )

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > PDF_MAX_SIZE_MB * 1024 * 1024:
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf",
                    error=f"PDF файл слишком большой (>{PDF_MAX_SIZE_MB}MB) по заголовку.",
                )

            pdf_content_bytes = await response.read()

            if len(pdf_content_bytes) > PDF_MAX_SIZE_MB * 1024 * 1024:
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf",
                    error=f"Скачанный PDF файл слишком большой (>{PDF_MAX_SIZE_MB}MB).",
                )

            loop = asyncio.get_running_loop()
            text_content = await loop.run_in_executor(
                None, _parse_pdf_bytes_sync, pdf_content_bytes
            )

            if text_content and not text_content.startswith("Ошибка"):
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf",
                    text_content=text_content,
                )
            else:
                error_msg = text_content if text_content else "Не удалось извлечь текст из PDF."
                return ExtractedContent(
                    source_url=url,
                    filename=filename,
                    content_type="pdf",
                    error=error_msg,
                )

    except aiohttp.ClientError as e:
        print(f"  DocParser: Ошибка сети (aiohttp) при скачивании '{filename}': {e}")
        return ExtractedContent(
            source_url=url,
            filename=filename,
            content_type="pdf",
            error=f"Ошибка сети: {type(e).__name__}",
        )
    except TimeoutError:
        print(f"  DocParser: Таймаут при скачивании '{filename}'")
        return ExtractedContent(
            source_url=url,
            filename=filename,
            content_type="pdf",
            error="Таймаут скачивания.",
        )
    except Exception as e:
        print(
            f"  DocParser: Общая ошибка обработки вложения '{filename}': {type(e).__name__} - {e}"
        )
        return ExtractedContent(
            source_url=url,
            filename=filename,
            content_type="pdf",
            error=f"Общая ошибка: {type(e).__name__}",
        )
