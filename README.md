# Homework review agent

Async worker that reads GetCourse homework threads, applies lesson-specific review prompts and posts feedback through Selenium. A Chrome extension manages prompts and connected accounts.

Python, OpenAI, Supabase and Selenium. The worker includes PDF attachment extraction.

## Run

Copy `.env.example` to `.env` and fill in the values. `ENCRYPTION_KEY` must contain 16, 24 or 32 UTF-8 bytes and match the key used by the account-management backend.

```sh
docker compose up --build
```

For local development, use Python 3.11+, Chrome and the dependencies in `requirements.txt`:

```sh
python -m venv .venv
python -m pip install -r requirements.txt
python getcourse-reply-bot/main_async.py
```

The Supabase deployment must provide the `getcourse_accounts`, `prompts`, `course_variables` and `bot_processing_state` tables, access policies and the Edge Functions called by the extension. That backend is not included here.

See `getcourse-extension/README.md` for extension build instructions. Worker service keys must never be used in the browser.

Cookies, downloads and runtime logs are excluded from Git.
