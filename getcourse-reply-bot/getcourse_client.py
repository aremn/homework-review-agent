import asyncio
import os
import pickle
import re
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from config import TIMESTAMP_FORMAT, WAIT_TIMEOUT
from pydantic import BaseModel, Field, ValidationError, field_validator
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from state_manager import DbStateManager
from webdriver_manager.chrome import ChromeDriverManager


class User(BaseModel):
    id: str | None = None
    name: str | None = None
    profile_image_url: str | None = Field(None)


class LessonInfo(BaseModel):
    id: str | None = None
    title: str | None = Field(None)
    training_name: str | None = None
    lesson_only_title: str | None = None
    url: str | None = Field(None)


class Attachment(BaseModel):
    type: str
    url: str
    filename: str | None = None


class TimestampInfo(BaseModel):
    precise_time_str: str | None = Field(None)
    relative_time_str: str | None = Field(None)
    url: str | None = Field(None)
    datetime_obj: datetime | None = Field(None, exclude=True)

    @field_validator("precise_time_str", mode="before")
    @classmethod
    def parse_precise_time(cls, v: str | None) -> str | None:
        if v:
            try:
                base_format = TIMESTAMP_FORMAT
                if "." in v:
                    base_format += ".%f"
                datetime.strptime(v[: len(base_format.replace("%f", "123456"))], base_format)
            except ValueError:
                try:
                    alt_format = "%Y-%m-%d %H:%M"
                    datetime.strptime(v[: len(alt_format)], alt_format)
                except ValueError:
                    pass
        return v

    @classmethod
    def parse_from_element(cls, element: Tag) -> "TimestampInfo":
        precise_str, relative_str, url_str, dt_obj = (None, None, None, None)
        answer_date_link = element.select_one(".answer-date a")
        if answer_date_link:
            title_attr = answer_date_link.get("title", "")
            precise_str = (
                title_attr.replace("Время обновления ответа", "").strip() if title_attr else None
            )
            relative_str = answer_date_link.text.strip()
            url_str = answer_date_link.get("href")
            if precise_str:
                try:
                    base_format = TIMESTAMP_FORMAT
                    if "." in precise_str:
                        base_format += ".%f"
                    dt_obj = datetime.strptime(
                        precise_str[: len(base_format.replace("%f", "123456"))],
                        base_format,
                    )
                    dt_obj = dt_obj.replace(tzinfo=UTC)
                except ValueError:
                    try:
                        alt_format = "%Y-%m-%d %H:%M"
                        dt_obj = datetime.strptime(precise_str[: len(alt_format)], alt_format)
                        dt_obj = dt_obj.replace(tzinfo=UTC)
                    except ValueError:
                        pass
        else:
            comment_time_span = element.select_one(".comment-time span.value")
            if comment_time_span:
                relative_str = comment_time_span.text.strip()
                parent_gc_comment = element.find_parent(class_="gc-comment")
                precise_from_attr = parent_gc_comment.get("title") if parent_gc_comment else None
                if precise_from_attr:
                    try:
                        dt_obj = datetime.fromtimestamp(int(precise_from_attr), tz=UTC)
                    except (ValueError, TypeError):
                        try:
                            base_format = TIMESTAMP_FORMAT
                            if "." in precise_from_attr:
                                base_format += ".%f"
                            dt_obj = datetime.strptime(
                                precise_from_attr[: len(base_format.replace("%f", "123456"))],
                                base_format,
                            )
                            dt_obj = dt_obj.replace(tzinfo=UTC)
                        except ValueError:
                            pass
        return cls(
            precise_time_str=precise_str or None,
            relative_time_str=relative_str,
            url=url_str,
            datetime_obj=dt_obj,
        )


class ExtractedContent(BaseModel):
    source_url: str
    filename: str | None = None
    content_type: str
    text_content: str | None = None
    error: str | None = None


class FoundTextLink(BaseModel):
    url: str
    link_text: str | None = None


class Message(BaseModel):
    id: str = Field(...)
    author: User
    timestamp: TimestampInfo
    text: str | None = Field(None)
    attachments: list[Attachment] = Field(default_factory=list)
    is_teacher_reply: bool = Field(False)
    found_text_links: list[FoundTextLink] = Field(default_factory=list)


class Thread(BaseModel):
    thread_id: str = Field(...)
    lesson: LessonInfo
    status_code: str | None = Field(None)
    status_text: str | None = Field(None)
    requires_attention: bool = Field(True)
    has_new_comments_indicator: bool = Field(False)
    messages: list[Message] = Field(...)

    @property
    def last_message_id(self) -> str | None:
        return self.messages[-1].id if self.messages else None


class AsyncGetCourseClient:
    def __init__(
        self,
        account_id: str,
        user_id: str,
        subdomain: str,
        email: str,
        password: str,
        state_manager: DbStateManager,
    ):
        self.account_id = account_id
        self.user_id = user_id
        self.subdomain = subdomain
        self._email = email
        self._password = password
        self.state_manager = state_manager
        self.base_url = f"https://{subdomain}.getcourse.ru/"
        self.login_url = f"https://{subdomain}.getcourse.ru/cms/system/login?required=true"
        self.target_answers_url = (
            f"https://{subdomain}.getcourse.ru/teach/control/answers/unanswered"
        )
        self.cookie_file = f"cookies_{self.subdomain}_{self.account_id}.pkl"
        self.cookies_dir = os.path.join(os.path.dirname(__file__), ".cookies")
        os.makedirs(self.cookies_dir, exist_ok=True)
        self.cookie_file_path = os.path.join(self.cookies_dir, self.cookie_file)
        self._driver: WebDriver | None = None
        self._wait: WebDriverWait | None = None
        self._lock = asyncio.Lock()
        self._login_failed = False
        self._session_validated = False
        self._cookies_loaded = False

    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    def _sync_start_driver(self):
        if self._driver is None:
            print(f"[{self.subdomain}] Инициализация WebDriver...")
            try:
                options = webdriver.ChromeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920x1080")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("log-level=3")
                options.page_load_strategy = "eager"
                options.add_argument(
                    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
                )
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option("useAutomationExtension", False)
                self.download_dir = os.path.join(
                    "/app", "gc_downloads", self.subdomain, self.account_id
                )
                os.makedirs(self.download_dir, exist_ok=True)
                prefs = {
                    "download.default_directory": self.download_dir,
                    "download.prompt_for_download": False,
                    "download.directory_upgrade": True,
                    "plugins.always_open_pdf_externally": True,
                }
                options.add_experimental_option("prefs", prefs)
                service = ChromeService(ChromeDriverManager().install())
                self._driver = webdriver.Chrome(service=service, options=options)
                self._driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    },
                )
                self._wait = WebDriverWait(self._driver, WAIT_TIMEOUT)
                print(f"[{self.subdomain}] WebDriver инициализирован.")
            except WebDriverException as e:
                print(f"[{self.subdomain}] Критическая ошибка WebDriver: {e}")
                if isinstance(e, SessionNotCreatedException):
                    print(f"[{self.subdomain}] Ошибка создания сессии: {e.msg}")
                raise

    async def _start_driver_async(self):
        async with self._lock:
            await self._run_sync(self._sync_start_driver)

    async def _quit_driver(self):
        async with self._lock:
            if self._driver:
                print(f"[{self.subdomain}] Закрытие WebDriver клиента...")
                try:
                    await self._run_sync(self._driver.quit)
                except Exception as e:
                    print(f"[{self.subdomain}] Ошибка при закрытии WebDriver клиента: {e}")
                finally:
                    self._driver = None
                    self._wait = None
                    print(f"[{self.subdomain}] WebDriver клиента закрыт.")

    @property
    async def driver_session(
        self,
    ) -> AsyncGenerator[tuple[WebDriver, WebDriverWait], None]:
        await self._start_driver_async()
        if not self._driver or not self._wait:
            raise ConnectionError(f"[{self.subdomain}] Не удалось запустить WebDriver для сессии.")
        try:
            yield (self._driver, self._wait)
        finally:
            await self._quit_driver()

    async def close(self):
        await self._quit_driver()

    async def __aenter__(self):
        await self._start_driver_async()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _sync_load_cookies(self) -> bool:
        if not self._driver:
            return False
        if self._cookies_loaded:
            return True
        path = self.cookie_file_path
        if not os.path.exists(path):
            return False
        print(f"[{self.subdomain}] Попытка загрузки cookies из {path}...")
        try:
            with open(path, "rb") as file:
                cookies = pickle.load(file)
            self._driver.get(self.base_url)
            time.sleep(0.5)
            for cookie in cookies:
                if "expiry" in cookie and isinstance(cookie["expiry"], float):
                    cookie["expiry"] = int(cookie["expiry"])
                if "domain" in cookie and cookie["domain"].startswith("."):
                    cookie["domain"] = cookie["domain"][1:]
                if self.subdomain in cookie.get("domain", ""):
                    try:
                        self._driver.add_cookie(cookie)
                    except Exception as e:
                        print(
                            f"  [{self.subdomain}] Warn: Не удалось добавить куку {cookie.get('name')}: {e}"
                        )
            print(f"[{self.subdomain}] Cookies загружены из {path}")
            self._cookies_loaded = True
            return True
        except Exception as e:
            print(f"[{self.subdomain}] Warn: Ошибка загрузки cookies ({e}). Удаление файла...")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as rm_e:
                    print(f"[{self.subdomain}] Warn: Не удалось удалить файл кук: {rm_e}")
            return False

    def _sync_save_cookies(self):
        if not self._driver:
            return
        path = self.cookie_file_path
        try:
            with open(path, "wb") as file:
                pickle.dump(self._driver.get_cookies(), file)
            print(f"[{self.subdomain}] Cookies сохранены в {path}")
        except Exception as e:
            print(f"[{self.subdomain}] Ошибка сохранения cookies: {e}")

    async def get_current_session_cookies_for_aiohttp(self) -> dict[str, str] | None:
        if not self._driver:
            print(
                f"[{self.subdomain}] Предупреждение: WebDriver не активен для get_current_session_cookies_for_aiohttp."
            )
            return None
        print(f"[{self.subdomain}] Получение кук для aiohttp из активной сессии...")
        try:
            all_selenium_cookies: list[dict[str, Any]] = await self._run_sync(
                self._driver.get_cookies
            )
            cookies_for_aiohttp: dict[str, str] = {}
            if all_selenium_cookies:
                for cookie_dict in all_selenium_cookies:
                    cookie_domain = cookie_dict.get("domain", "")
                    parsed_base_url_netloc = urlparse(self.base_url).netloc
                    is_matching_domain = False
                    if not cookie_domain or parsed_base_url_netloc.endswith(
                        cookie_domain.lstrip(".")
                    ):
                        is_matching_domain = True
                    elif parsed_base_url_netloc == cookie_domain:
                        is_matching_domain = True
                    if is_matching_domain:
                        cookies_for_aiohttp[cookie_dict["name"]] = cookie_dict["value"]
            if cookies_for_aiohttp:
                print(f"[{self.subdomain}] Получено {len(cookies_for_aiohttp)} кук для aiohttp.")
            else:
                print(f"[{self.subdomain}] Предупреждение: не найдено подходящих кук.")
            return cookies_for_aiohttp
        except WebDriverException as e:
            print(f"[{self.subdomain}] Ошибка WebDriver при получении кук: {e}")
        except Exception as e:
            print(f"[{self.subdomain}] Неожиданная ошибка при получении кук: {e}")
        return None

    async def _download_and_parse_docx_with_selenium(
        self, attachment: Attachment, driver: WebDriver, wait: WebDriverWait
    ) -> str | None:
        if not attachment.url or not (
            attachment.filename and attachment.filename.lower().endswith(".docx")
        ):
            return None
        print(
            f"  SeleniumDownloader: Попытка скачивания DOCX: {attachment.filename} с {attachment.url[:70]}..."
        )
        expected_filepath = os.path.join(self.download_dir, attachment.filename)
        alt_download_dirs = [
            self.download_dir,
            os.path.expanduser("~/Downloads"),
            "/tmp",
            "/downloads",
        ]
        print(f"  SeleniumDownloader: Ожидаемый путь: {expected_filepath}")
        print(f"  SeleniumDownloader: Проверяемые папки: {alt_download_dirs}")
        for d in alt_download_dirs:
            try:
                f = os.path.join(d, attachment.filename)
                if os.path.exists(f):
                    os.remove(f)
            except Exception as e:
                print(f"  SeleniumDownloader: Не удалось удалить старый файл {f}: {e}")
        temp_download_filepath = expected_filepath + ".crdownload"
        try:
            await self._run_sync(driver.get, attachment.url)
            timeout_seconds = 60
            start_time = time.monotonic()
            download_complete = False
            found_filepath = None
            while time.monotonic() - start_time < timeout_seconds:
                for d in alt_download_dirs:
                    fpath = os.path.join(d, attachment.filename)
                    crdownload = fpath + ".crdownload"
                    if os.path.exists(fpath) and (not os.path.exists(crdownload)):
                        await asyncio.sleep(1)
                        current_size = os.path.getsize(fpath)
                        await asyncio.sleep(2)
                        if current_size > 0 and current_size == os.path.getsize(fpath):
                            print(
                                f"  SeleniumDownloader: Найден скачанный DOCX: {attachment.filename} в {d}"
                            )
                            download_complete = True
                            found_filepath = fpath
                            break
                if download_complete:
                    break
                await asyncio.sleep(1)
            if not download_complete:
                print(
                    f"  SeleniumDownloader: Таймаут ожидания скачивания файла {attachment.filename}"
                )
                for d in alt_download_dirs:
                    try:
                        files_in_dir = os.listdir(d)
                        print(f"  SeleniumDownloader: Файлы в папке {d}: {files_in_dir}")
                    except Exception as e:
                        print(f"  SeleniumDownloader: Не удалось получить список файлов в {d}: {e}")
                if os.path.exists(temp_download_filepath):
                    try:
                        os.remove(temp_download_filepath)
                    except Exception:
                        pass
                return f"[Ошибка: Таймаут скачивания {attachment.filename}]"
            print(
                f"  SeleniumDownloader: Файл {os.path.basename(found_filepath)} скачан в {found_filepath}"
            )

            def parse_docx_sync(filepath: str) -> str | None:
                try:
                    from docx import Document

                    doc = Document(filepath)
                    full_text = [para.text for para in doc.paragraphs]
                    return "\n".join(full_text)
                except Exception as e_parse:
                    print(f"  SeleniumDownloader: Ошибка парсинга DOCX {filepath}: {e_parse}")
                    return f"[Ошибка парсинга DOCX: {e_parse}]"
                finally:
                    if os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                        except OSError:
                            print(f"  SeleniumDownloader: Не удалось удалить {filepath}")

            loop = asyncio.get_running_loop()
            text_content = await loop.run_in_executor(None, parse_docx_sync, found_filepath)
            return text_content
        except WebDriverException as e:
            print(
                f"  SeleniumDownloader: Ошибка WebDriver при попытке скачать {attachment.filename}: {e}"
            )
            return f"[Ошибка WebDriver при скачивании: {e}]"
        except Exception as e:
            print(
                f"  SeleniumDownloader: Общая ошибка при скачивании/парсинге DOCX {attachment.filename}: {e}"
            )
            return f"[Общая ошибка DOCX: {e}]"

    def _sync_perform_login(self) -> bool:
        if not self._driver or not self._wait:
            print(f"[{self.subdomain}] Ошибка: WebDriver не инициализирован для логина.")
            return False
        self._login_failed = False
        print(f"[{self.subdomain}] Проверка сессии и попытка входа (sync)...")
        if self._sync_load_cookies():
            print(
                f"[{self.subdomain}] Cookies загружены. Проверка валидности на {self.target_answers_url}..."
            )
            self._driver.get(self.target_answers_url)
            time.sleep(1)
            current_url_after_cookie_load = self._driver.current_url
            if "/login" not in current_url_after_cookie_load:
                print(f"[{self.subdomain}] Вход с использованием cookies успешен.")
                return True
            else:
                print(
                    f"[{self.subdomain}] Cookies недействительны или истекли (перенаправлено на {current_url_after_cookie_load})."
                )
                if os.path.exists(self.cookie_file_path):
                    try:
                        os.remove(self.cookie_file_path)
                        print(
                            f"[{self.subdomain}] Удалены невалидные cookies: {self.cookie_file_path}"
                        )
                    except Exception as e:
                        print(f"[{self.subdomain}] Не удалось удалить файл cookies: {e}")
                self._cookies_loaded = False
        else:
            print(f"[{self.subdomain}] Файл cookies не найден или произошла ошибка загрузки.")
        print(f"[{self.subdomain}] Вход с использованием email/пароля...")
        self._driver.get(self.login_url)
        if not self._email or not self._password:
            print(f"[{self.subdomain}] Ошибка: Email или пароль не предоставлены для входа.")
            self._login_failed = True
            return False
        print(f"[{self.subdomain}] Используется Email: {self._email}")
        try:
            login_form_selector = "#xdget99945"
            self._wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, login_form_selector)),
                message=f"Форма {login_form_selector} не найдена/невидима",
            )
            email_input_selector = f'{login_form_selector} input[name="email"]'
            pw_input_selector = f'{login_form_selector} input[name="password"]'
            button_selector = f"{login_form_selector} button.btn-success"
            email_input = self._wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, email_input_selector)),
                message=f"Поле Email {email_input_selector} не найдено/не кликабельно",
            )
            pw_input = self._wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, pw_input_selector)),
                message=f"Поле Пароль {pw_input_selector} не найдено/не кликабельно",
            )
            email_input.clear()
            time.sleep(0.7)
            email_input.send_keys(self._email)
            time.sleep(0.7)
            pw_input.clear()
            pw_input.send_keys(self._password)
            time.sleep(0.7)
            btn = self._wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, button_selector)),
                message=f"Кнопка {button_selector} не найдена/не кликабельна",
            )
            try:
                btn.click()
            except Exception as click_e:
                print(f"[{self.subdomain}] Warn: Ошибка клика, пробую JS ({click_e})...")
                self._driver.execute_script("arguments[0].click();", btn)
            print(f"[{self.subdomain}] Ожидание результата после входа...")
            try:
                self._wait.until_not(
                    EC.url_contains("/login"),
                    message="Остались на странице логина после попытки входа.",
                )
                if "/login" in self._driver.current_url:
                    print(
                        f"[{self.subdomain}] ОШИБКА: Не удалось покинуть страницу логина! Проверьте правильность логина и пароля."
                    )
                    self._login_failed = True
                    if os.path.exists(self.cookie_file_path):
                        try:
                            os.remove(self.cookie_file_path)
                            print(
                                f"[{self.subdomain}] Удалены куки после неудачного логина: {self.cookie_file_path}"
                            )
                        except Exception as e:
                            print(f"[{self.subdomain}] Не удалось удалить файл куки: {e}")
                    self._cookies_loaded = False
                    self._session_validated = False
                    return False
                print(f"[{self.subdomain}] Вход по email/паролю успешен!")
                self._sync_save_cookies()
                self._session_validated = True
                return True
            except TimeoutException as e:
                print(
                    f"[{self.subdomain}] ОШИБКА: Таймаут ожидания смены URL после логина. {e.msg} Проверьте правильность логина и пароля."
                )
                self._login_failed = True
                if os.path.exists(self.cookie_file_path):
                    try:
                        os.remove(self.cookie_file_path)
                        print(
                            f"[{self.subdomain}] Удалены куки после неудачного логина: {self.cookie_file_path}"
                        )
                    except Exception as e:
                        print(f"[{self.subdomain}] Не удалось удалить файл куки: {e}")
                self._cookies_loaded = False
                self._session_validated = False
                return False
        except (TimeoutException, NoSuchElementException) as e:
            print(
                f"[{self.subdomain}] Ошибка входа (элемент формы не найден или таймаут): {getattr(e, 'msg', str(e))}. Проверьте правильность логина и пароля."
            )
            self._login_failed = True
            self._cookies_loaded = False
            self._session_validated = False
            return False
        except Exception as e:
            print(
                f"[{self.subdomain}] Непредвиденная ошибка при входе по email/паролю: {e}. Проверьте правильность логина и пароля."
            )
            self._login_failed = True
            self._cookies_loaded = False
            self._session_validated = False
            return False

    @property
    def login_failed(self) -> bool:
        return self._login_failed

    async def _ensure_logged_in(self):
        async with self._lock:
            if self._session_validated:
                return
            if self._driver is None:
                await self._run_sync(self._sync_start_driver)
            if self._driver is None:
                raise ConnectionError(f"[{self.subdomain}] Не удалось запустить WebDriver.")
            needs_login = False
            try:
                current_url = await self._run_sync(getattr, self._driver, "current_url")
                if "/login" in current_url or current_url == "data:,":
                    needs_login = True
                else:
                    try:
                        await self._run_sync(
                            self._driver.find_element, By.CSS_SELECTOR, "body.logged-in"
                        )
                    except (NoSuchElementException, WebDriverException):
                        needs_login = True
            except WebDriverException as e:
                print(f"[{self.subdomain}] Ошибка WebDriver при проверке сессии: {e}. Перезапуск.")
                await self._run_sync(self._sync_start_driver)
                if self._driver is None:
                    raise ConnectionError(
                        f"[{self.subdomain}] Не удалось перезапустить WebDriver."
                    ) from e
                needs_login = True
            if needs_login:
                print(f"[{self.subdomain}] Сессия не активна. Попытка входа...")
                login_success = await self._run_sync(self._sync_perform_login)
                if not login_success:
                    raise ConnectionError(f"[{self.subdomain}] Не удалось войти в GetCourse.")
            current_url_after_login = await self._run_sync(getattr, self._driver, "current_url")
            if "/login" in current_url_after_login:
                raise ConnectionError(f"[{self.subdomain}] Сессия не активна ПОСЛЕ попытки входа.")
            self._session_validated = True

    def _safe_get_text(self, element: Tag | None, selector: str) -> str | None:
        if not element:
            return None
        found = element.select_one(selector)
        return found.text.strip() if found else None

    def _safe_get_attr(self, element: Tag | None, selector: str, attribute: str) -> str | None:
        if not element:
            return None
        found = element.select_one(selector)
        return found.get(attribute) if found else None

    def _parse_user(self, element: Tag) -> User:
        user_link = element.select_one("a.user-profile-link")
        img_src = self._safe_get_attr(element, ".user-image img, .user-image-wrapper img", "src")
        user_id = None
        user_name = None
        if user_link:
            user_id = user_link.get("data-user-id")
            user_name = user_link.text.strip()
            if not user_id:
                href = user_link.get("href", "")
                match = re.search("/id/(\\d+)", href)
                if match:
                    user_id = match.group(1)
        elif element.get("data-user-id"):
            user_id = element.get("data-user-id")
        if not user_name and user_id:
            name_element = element.select_one(".title > a.user-profile-link")
            if name_element:
                user_name = name_element.text.strip()
            else:
                title_element = element.select_one(".title")
                if title_element:
                    temp_soup = BeautifulSoup(str(title_element), "lxml")
                    temp_tag = temp_soup.find()
                    if temp_tag:
                        for child in temp_tag.find_all(["span", "a"], recursive=False):
                            child.decompose()
                        cleaned_name = temp_tag.text.strip()
                        if cleaned_name:
                            user_name = cleaned_name
        return User(id=user_id, name=user_name, profile_image_url=img_src)

    def _parse_timestamp(self, element: Tag) -> TimestampInfo:
        return TimestampInfo.parse_from_element(element)

    def _parse_message(self, msg_element: Tag) -> Message | None:
        msg_id_attr = msg_element.get("data-id") or msg_element.get("data-object-id")
        if not msg_id_attr:
            print(f"[{self.subdomain}] Warn: Message element without data-id found.")
            return None
        msg_id: str = str(msg_id_attr)
        is_teacher = "teacher-answer" in msg_element.get(
            "class", []
        ) or "teacher-comment" in msg_element.get("class", [])
        author = self._parse_user(msg_element)
        timestamp = self._parse_timestamp(msg_element)
        text_content_parts: list[str] = []
        found_links_from_text: list[FoundTextLink] = []
        text_container_div = msg_element.select_one(
            ".answer-text, .comment-text, div.text > div.main > .emoji-text, div.text > div.main > div.text-content"
        )
        if text_container_div:
            for content_element in text_container_div.contents:
                if isinstance(content_element, NavigableString):
                    text_content_parts.append(str(content_element))
                elif isinstance(content_element, Tag):
                    if content_element.name == "a":
                        href = content_element.get("href")
                        link_text = content_element.get_text(strip=True)
                        if href and isinstance(href, str):
                            parsed_href = urlparse(href)
                            if (
                                parsed_href.scheme in ["http", "https"]
                                and parsed_href.netloc
                                and ("getcourse.ru/user/" not in href.lower())
                                and ("getcourse.ru/pl/teach/" not in href.lower())
                                and (not href.startswith("#"))
                            ):
                                found_links_from_text.append(
                                    FoundTextLink(
                                        url=href,
                                        link_text=link_text if link_text != href else None,
                                    )
                                )
                        text_content_parts.append(content_element.get_text())
                    elif content_element.name == "br":
                        text_content_parts.append("\n")
                    else:
                        text_content_parts.append(content_element.get_text())
        full_text_content = "".join(text_content_parts).strip()
        if not full_text_content:
            full_text_content = None
        parsed_attachments: list[Attachment] = []
        file_boxes = msg_element.select("div.file.answer-file-box, div.file.comment-file-box")
        print(
            f"  [{self.subdomain}] Message {msg_id}: Found {len(file_boxes)} file_boxes. HTML of boxes: {[str(b) for b in file_boxes]}"
        )
        for box in file_boxes:
            link_tag = box.select_one("div.simple-filelist-filename a")
            print(
                f"  [{self.subdomain}] Message {msg_id}, Box: Found link_tag: {(str(link_tag) if link_tag else 'None')}"
            )
            if link_tag and isinstance(link_tag, Tag):
                file_url_attr = link_tag.get("href")
                filename_text = link_tag.text.strip()
                if file_url_attr and isinstance(file_url_attr, str):
                    file_url = urlparse(file_url_attr)
                    if not file_url.scheme:
                        full_file_url = self.base_url.rstrip("/") + file_url.path
                    else:
                        full_file_url = file_url_attr
                    file_type = "unknown"
                    if filename_text.lower().endswith(".pdf"):
                        file_type = "pdf"
                    elif filename_text.lower().endswith(".docx"):
                        file_type = "docx"
                    elif any(
                        filename_text.lower().endswith(ext)
                        for ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
                    ):
                        file_type = "image"
                    parsed_attachments.append(
                        Attachment(type=file_type, url=full_file_url, filename=filename_text)
                    )
                    print(
                        f"  [{self.subdomain}] Parsed Attachment: name='{filename_text}', url='{full_file_url}', type='{file_type}' for message {msg_id}"
                    )
                else:
                    print(
                        f"  [{self.subdomain}] Warn: Attachment link_tag for message {msg_id} has no valid href."
                    )
            else:
                print(
                    f"  [{self.subdomain}] Warn: Could not find attachment link_tag in file_box for message {msg_id}."
                )
        try:
            return Message(
                id=msg_id,
                author=author,
                timestamp=timestamp,
                text=full_text_content,
                attachments=parsed_attachments,
                found_text_links=found_links_from_text,
                is_teacher_reply=is_teacher,
            )
        except ValidationError as e:
            print(
                f"  [{self.subdomain}] Pydantic Validation Error creating Message ID {msg_id}: {e}"
            )
            return None

    def _parse_lesson_info(self, thread_wrapper_div: Tag) -> LessonInfo:
        lesson_id = thread_wrapper_div.get("data-lesson-id")
        lesson_link = thread_wrapper_div.select_one("a.show-lesson-link")
        lesson_title, lesson_url, training_name, lesson_only_title = (
            None,
            None,
            None,
            None,
        )
        if lesson_link:
            lesson_title_full = lesson_link.text.strip()
            lesson_url = lesson_link.get("href")
            training_span = lesson_link.find("span", class_="text-muted")
            if training_span:
                training_name = training_span.text.strip()
                cloned_link = BeautifulSoup(str(lesson_link), "lxml").a
                if cloned_link and cloned_link.span:
                    cloned_link.span.decompose()
                    lesson_only_title = cloned_link.text.strip()
                else:
                    lesson_only_title = lesson_title_full.replace(training_name, "").strip()
                lesson_title = f"{training_name} {lesson_only_title}"
            else:
                lesson_title = lesson_title_full
                lesson_only_title = lesson_title_full
        elif thread_wrapper_div.get("data-lesson-name"):
            lesson_title = thread_wrapper_div.get("data-lesson-name")
            lesson_only_title = lesson_title
        return LessonInfo(
            id=lesson_id,
            title=lesson_title,
            training_name=training_name,
            lesson_only_title=lesson_only_title,
            url=lesson_url,
        )

    def _parse_thread_status(self, thread_wrapper_div: Tag) -> tuple[str | None, str | None]:
        classes = thread_wrapper_div.get("class", [])
        status_code = next(
            (c.replace("status-", "") for c in classes if c.startswith("status-")), None
        )
        status_div = thread_wrapper_div.select_one(".answer-status-label, .status-label")
        status_text = status_div.text.strip().replace(" ↓", "").strip() if status_div else None
        return (status_code, status_text)

    def _parse_initial_message(self, thread_wrapper_div: Tag) -> Message | None:
        return self._parse_message(thread_wrapper_div)

    def _parse_replies(self, thread_wrapper_div: Tag) -> list[Message]:
        messages: list[Message] = []
        container = thread_wrapper_div.select_one(
            ".comments .comment-list, .comments-tree .comments-container"
        )
        if not container:
            return messages
        reply_elements = container.select("div.comment.lesson-answer-comment[data-id]")
        for element in reply_elements:
            msg = self._parse_message(element)
            if msg:
                messages.append(msg)
        return messages

    def _has_new_comments_indicator(self, thread_wrapper_div: Tag) -> bool:
        controls_div = thread_wrapper_div.select_one("div.answer-controls")
        if not controls_div:
            return False
        mark_read_button = controls_div.select_one('button[data-action="set_reviewed_at"]')
        return mark_read_button is not None

    def _parse_thread(self, thread_wrapper_div: Tag) -> Thread | None:
        thread_id = thread_wrapper_div.get("data-id")
        if not thread_id:
            return None
        lesson = self._parse_lesson_info(thread_wrapper_div)
        status_code, status_text = self._parse_thread_status(thread_wrapper_div)
        initial_message = self._parse_initial_message(thread_wrapper_div)
        if not initial_message:
            print(f"[{self.subdomain}] Warn: Не удалось спарсить начальное сообщение {thread_id}")
            return None
        replies = self._parse_replies(thread_wrapper_div)
        all_messages = [initial_message] + replies
        has_new_comments = self._has_new_comments_indicator(thread_wrapper_div)
        try:
            return Thread(
                thread_id=thread_id,
                lesson=lesson,
                status_code=status_code,
                status_text=status_text,
                messages=all_messages,
                has_new_comments_indicator=has_new_comments,
            )
        except ValidationError as e:
            print(f"[{self.subdomain}] Pydantic Error для треда {thread_id}: {e}")
            return None

    def _sync_post_reply(self, thread_id: str, reply_text: str) -> bool:
        if not self._driver or not self._wait:
            return False
        print(f"[{self.subdomain}] Попытка ответа на тред ID: {thread_id}...")
        try:
            wrapper_selector = f"div.answer_wrapper[data-id='{thread_id}']"
            thread_div = self._wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wrapper_selector))
            )
            form_selector = f"#commentReplyForm-{thread_id}-8"
            try:
                form = thread_div.find_element(By.CSS_SELECTOR, form_selector)
            except NoSuchElementException:
                form = thread_div.find_element(By.CSS_SELECTOR, "div.gc-comment-form")
            textarea = form.find_element(By.CSS_SELECTOR, "textarea.new-comment-textarea")
            button = form.find_element(By.CSS_SELECTOR, "button.btn-send")
            wait_textarea_selector = (
                f"{form_selector} textarea.new-comment-textarea"
                if form_selector.startswith("#")
                else f"{wrapper_selector} div.gc-comment-form textarea.new-comment-textarea"
            )
            wait_button_selector = (
                f"{form_selector} button.btn-send"
                if form_selector.startswith("#")
                else f"{wrapper_selector} div.gc-comment-form button.btn-send"
            )
            self._wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, wait_textarea_selector)))
            textarea.send_keys(reply_text)
            time.sleep(0.3)
            self._wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, wait_button_selector)))
            try:
                self._driver.execute_script("arguments[0].scrollIntoView(true);", button)
                time.sleep(0.2)
                button.click()
            except ElementClickInterceptedException:
                print(f"[{self.subdomain}] Warn: Клик перехвачен, пробую JS...")
                self._driver.execute_script("arguments[0].click();", button)
            except Exception as click_e:
                print(f"[{self.subdomain}] Warn: Ошибка клика, пробую JS ({click_e})...")
                self._driver.execute_script("arguments[0].click();", button)
            time.sleep(2)
            print(f"  [{self.subdomain}] Ответ на тред {thread_id} отправлен.")
            return True
        except (TimeoutException, NoSuchElementException) as e:
            print(f"[{self.subdomain}] Ошибка формы ответа {thread_id}: {e}")
            return False
        except Exception as e:
            print(f"[{self.subdomain}] Непредвиденная ошибка при ответе {thread_id}: {e}")
            return False

    def _sync_click_action_button(self, thread_id: str, data_action: str) -> bool:
        if not self._driver or not self._wait:
            return False
        print(f"[{self.subdomain}] Клик [data-action='{data_action}'] для {thread_id}...")
        try:
            wrapper_selector = f"div.answer_wrapper[data-id='{thread_id}']"
            thread_div = self._wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wrapper_selector))
            )
            button_selector = f'button[data-action="{data_action}"]'
            action_button = thread_div.find_element(
                By.CSS_SELECTOR, f"div.answer-controls {button_selector}"
            )
            wait_button_selector = f"{wrapper_selector} div.answer-controls {button_selector}"
            self._wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, wait_button_selector)))
            try:
                self._driver.execute_script("arguments[0].scrollIntoView(true);", action_button)
                time.sleep(0.2)
                action_button.click()
            except ElementClickInterceptedException:
                print(f"[{self.subdomain}] Warn: Клик перехвачен, пробую JS...")
                self._driver.execute_script("arguments[0].click();", action_button)
            except Exception as click_e:
                print(f"[{self.subdomain}] Warn: Ошибка клика, пробую JS ({click_e})...")
                self._driver.execute_script("arguments[0].click();", action_button)
            time.sleep(1.5)
            print(f"  [{self.subdomain}] Кнопка [{data_action}] для {thread_id} нажата.")
            return True
        except (TimeoutException, NoSuchElementException):
            print(f"  [{self.subdomain}] Кнопка [{data_action}] не найдена/активна {thread_id}.")
            return False
        except Exception as e:
            print(f"  [{self.subdomain}] Ошибка нажатия [{data_action}] {thread_id}: {e}")
            return False

    def _sync_scrape_page(self, url) -> list[Thread]:
        if not self._driver or not self._wait:
            return []
        print(f"[{self.subdomain}] Скрапинг страницы: {url}...")
        threads = []
        try:
            self._driver.get(url)
            self._wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.answers-list")))
            time.sleep(4)
            print(f"[{self.subdomain}] Парсинг страницы ответов...")
            soup = BeautifulSoup(self._driver.page_source, "lxml")
            wrappers = soup.find_all("div", class_="answer_wrapper")
            print(f"[{self.subdomain}] Найдено {len(wrappers)} блоков тредов.")
            threads = [
                parsed
                for wrapper in wrappers
                if (parsed := self._parse_thread(wrapper)) is not None
            ]
        except TimeoutException:
            print(f"[{self.subdomain}] Ошибка: Таймаут ожидания на {url}.")
        except Exception as e:
            print(f"[{self.subdomain}] Ошибка скрапинга {url}: {e}")
        return threads

    async def get_threads_to_process(self) -> list[Thread]:
        await self._start_driver_async()
        if not self._driver or not self._wait:
            print(f"[{self.subdomain}] Ошибка: WebDriver не готов.")
            return []
        try:
            await self._ensure_logged_in()
            print(f"[{self.subdomain}] Получение тредов для обработки...")
            all_threads = await self._run_sync(
                lambda url=self.target_answers_url: self._sync_scrape_page(url)
            )
            if not all_threads:
                print(f"[{self.subdomain}] Треды не найдены.")
                return []
            threads_to_process: list[Thread] = []
            thread_ids_on_page = [t.thread_id for t in all_threads if t.thread_id]
            if not thread_ids_on_page:
                print(f"[{self.subdomain}] Нет тредов с ID.")
                return []
            tasks = [
                self.state_manager.get_last_message_id(self.subdomain, thread_id)
                for thread_id in thread_ids_on_page
            ]
            last_known_ids = await asyncio.gather(*tasks)
            last_known_map = dict(zip(thread_ids_on_page, last_known_ids, strict=False))
            for thread in all_threads:
                if not thread.thread_id:
                    continue
                last_known_message_id = last_known_map.get(thread.thread_id)
                current_last_message_id = thread.last_message_id
                is_completely_new = last_known_message_id is None
                has_new_messages = (
                    last_known_message_id is not None
                    and current_last_message_id is not None
                    and (last_known_message_id != current_last_message_id)
                )
                ui_indicates_new = thread.has_new_comments_indicator or thread.status_code == "new"
                if (
                    is_completely_new
                    or has_new_messages
                    or (ui_indicates_new and (not has_new_messages))
                ):
                    threads_to_process.append(thread)
                    reason = f"new_db={is_completely_new}, new_msg={has_new_messages}, ui_new={ui_indicates_new}"
                    print(f"  [{self.subdomain}] Тред {thread.thread_id} к обработке ({reason})")
            print(f"[{self.subdomain}] Найдено {len(threads_to_process)} тредов для обработки.")
            return threads_to_process
        finally:
            pass

    async def process_thread_response(self, thread: Thread, response_text: str) -> bool:
        await self._start_driver_async()
        if not self._driver or not self._wait:
            print(f"[{self.subdomain}] Ошибка: WebDriver не готов.")
            return False
        success_overall = False
        try:
            await self._ensure_logged_in()
            current_url = await self._run_sync(getattr, self._driver, "current_url")
            if self.target_answers_url not in current_url:
                print(f"[{self.subdomain}] Переход на {self.target_answers_url}...")
                await self._run_sync(self._driver.get, self.target_answers_url)
                try:
                    await self._run_sync(
                        lambda: self._wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.answers-list"))
                        )
                    )
                    await asyncio.sleep(1.5)
                except TimeoutException:
                    print(f"[{self.subdomain}] Ошибка: таймаут загрузки {self.target_answers_url}.")
                    return False
            post_success = await self._run_sync(
                self._sync_post_reply, thread.thread_id, response_text
            )
            if post_success:
                mark_success = False
                if thread.has_new_comments_indicator:
                    mark_success = await self._run_sync(
                        self._sync_click_action_button,
                        thread.thread_id,
                        "set_reviewed_at",
                    )
                elif thread.status_code == "new":
                    mark_success = await self._run_sync(
                        self._sync_click_action_button, thread.thread_id, "viewed"
                    )
                if not mark_success and (
                    thread.has_new_comments_indicator or thread.status_code == "new"
                ):
                    print(
                        f"[{self.subdomain}] Warn: Не удалось нажать кнопку 'просмотрено/прочитано' {thread.thread_id}."
                    )
                if thread.last_message_id:
                    await self.state_manager.update_last_message_id(
                        self.subdomain, thread.thread_id, thread.last_message_id
                    )
                else:
                    print(
                        f"[{self.subdomain}] Warn: Нет last_message_id для обновления {thread.thread_id}"
                    )
                success_overall = True
            return success_overall
        finally:
            if not success_overall:
                print(f"[{self.subdomain}] Ошибка обработки ответа для треда {thread.thread_id}.")
