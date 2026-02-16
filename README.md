# Receipt Processor

A PWA that lets you snap a photo of a receipt, parse it with AI vision, and append it as a row to a shared Google Sheet. Built for household expense tracking with Bulgarian-language categories.

## How it works

1. Open the PWA on your phone and authenticate with a shared PIN
2. Tap the camera icon to take a photo of a receipt
3. The image is sent to an AI vision model (Claude, Gemini, or Grok) which extracts the date, total, category, and a brief description
4. A new row is written to your Google Sheet with the parsed data
5. You can edit the category, payment method, or notes inline — changes update the sheet in real time
6. Undo removes the last entry

## Architecture

```
Phone Browser (PWA) → FastAPI Backend → AI Vision API → Google Sheets API
```

- **Frontend**: vanilla HTML/CSS/JS PWA, mobile-first, no build step
- **Backend**: Python FastAPI serving the API and static files
- **AI providers**: Claude (Anthropic), Gemini (Google), Grok (xAI) — selectable per receipt
- **Storage**: Google Sheets via `gspread` with service account auth

Receipts are parsed in EUR (Bulgaria switched to Euro in January 2026). A BGN column is kept for backward compatibility, calculated at the fixed rate of 1 EUR = 1.95583 BGN.

## Prerequisites

1. **Google Cloud**: create a project, enable the Sheets API, create a service account, and download the JSON key as `service_account.json`
2. **Google Sheet**: share it with the service account email (Editor access)
3. **AI API key**: at least one of Anthropic, Google AI, or xAI

## Setup

```bash
cp .env.example .env
# Edit .env with your API keys, sheet ID, and auth PIN
```

Place `service_account.json` in the project root.

### Run with Docker (recommended)

```bash
docker compose up -d --build
```

To rebuild after pulling changes:

```bash
./rebuild.sh
```

### Run locally

```bash
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

The app will be available at `http://localhost:8000`.

## Configuration

| Variable | Description |
|---|---|
| `VISION_PROVIDER` | Default AI provider: `claude`, `gemini`, or `grok` |
| `ANTHROPIC_API_KEY` | Anthropic API key (required if using Claude) |
| `GOOGLE_API_KEY` | Google AI API key (required if using Gemini) |
| `XAI_API_KEY` | xAI API key (required if using Grok) |
| `GOOGLE_SHEETS_ID` | Google Sheets spreadsheet ID |
| `GOOGLE_SHEETS_WORKSHEET` | Worksheet name (default: `Sheet1`) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to service account JSON (default: `service_account.json`) |
| `AUTH_TOKEN` | Shared PIN for web interface access |

## Google Sheet columns

| Column | Source |
|---|---|
| Дата | Parsed from receipt (DD.MM.YYYY) |
| Категория | AI-classified from predefined list |
| Цена лв | Calculated: EUR * 1.95583 |
| Цена € | Parsed total from receipt |
| GGBG лв | Empty (filled manually) |
| Плащане | AI guess or user selection |
| Допълн. такса | Empty (filled manually) |
| Payback | Empty (filled manually) |
| Пояснения | AI-generated summary |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Nginx reverse proxy

Example configuration that serves the app over HTTPS and restricts access to the `10.20.0.0/16` network:

```nginx
server {
    listen 443 ssl;
    server_name receipts.example.com;

    ssl_certificate     /etc/nginx/ssl/receipts.crt;
    ssl_certificate_key /etc/nginx/ssl/receipts.key;

    # Restrict access to internal network
    allow 10.20.0.0/16;
    deny all;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Support large receipt images
        client_max_body_size 10M;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name receipts.example.com;

    allow 10.20.0.0/16;
    deny all;

    return 301 https://$host$request_uri;
}
```
