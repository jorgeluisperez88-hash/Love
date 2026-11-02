# PS5 Pro Stock Tracker

Checks Sony and authorized US retailers on a schedule for **PlayStation 5 Pro**
consoles and **PS5 Pro bundles**, and alerts your phone, Discord, Telegram or
email when one can be bought.

| Retailer | How it's checked |
|---|---|
| PlayStation Direct | product page (headless browser) |
| Best Buy | official Products API if `BESTBUY_API_KEY` is set, otherwise search + product pages |
| Walmart | search + product pages; **Walmart.com as the seller only** |
| Target | search + product pages (headless browser) |
| Amazon | search + product pages; **sold by Amazon.com only** |
| GameStop | search + product pages (headless browser); pre-owned ignored |
| Costco, Sam's Club | search + product pages (headless browser) |
| Newegg | search + product pages; **sold by Newegg only** |
| Antonline | search + product pages |
| Micro Center | off by default (mostly in-store); enable in `retailers.yaml` |

## How it works

1. **Find listings.** For each retailer it loads a search for "ps5 pro" and
   collects links whose title or URL says *PS5 Pro* / *PlayStation 5 Pro*,
   skipping accessories (stands, cases, skins, covers), pre-owned or
   refurbished units, and games labeled "PS5 Pro Enhanced". Any product URLs
   you list under `products:` are always checked too. New bundles are picked
   up automatically.
2. **Check stock** on each product page:
   - the schema.org data retailers publish for Google (`InStock` / `OutOfStock` / `PreOrder`),
   - availability `<meta>` tags,
   - the purchase buttons ("Add to Cart" vs "Sold Out" / "Coming Soon"),
     ignoring buttons in "customers also bought" carousels.
3. **Filter out scalpers.** An in-stock item is only alerted if it's priced
   between `min_price` and `max_price` (default $500–$1,050) and, for
   marketplace sites, is sold by the retailer itself.
4. **Alert once per restock.** State is saved in `state/state.json`, so you get
   one alert when an item comes back, plus a reminder every
   `realert_after_minutes` (default 60) while it stays in stock. If a check is
   blocked or fails, the last known status is kept, so a failure can't cause
   a duplicate alert.

Each run prints a table (also shown in the GitHub Actions run summary):

```
| | Retailer | Status | Item | Price | Notes |
| 🟢 | PlayStation Direct | IN_STOCK | PlayStation 5 Pro Console | $899.99 | json-ld availability |
| ⚪ | Walmart | IGNORED | PS5 Pro Console | $1,469.00 | price outside $500-$1,050 (likely scalper) |
| 🔴 | Target | OUT_OF_STOCK | PlayStation 5 Pro Console | $899.99 | purchase buttons |
| 🟠 | Amazon | BLOCKED | — | — | HTTP 503 - bot protection |
```

## Status page

Every run also builds a web page showing each store's latest status, with a
**Go to store** button on anything in stock. It works on phones, follows your
light/dark setting and reloads itself every 5 minutes.

**Turn it on (one time):** in the repo go to **Settings → Pages** and under
**Build and deployment → Source** choose **GitHub Actions**. After the next run
the page is at `https://<your-username>.github.io/<repo>/`, and each run's
summary links to it. Bookmark it or add it to your phone's home screen.

GitHub Pages is free for public repos. Private repos need a paid GitHub plan.

Running locally, the page is written to `site/index.html`; open it in a browser.

## Alerts

Set any of these as environment variables locally, or as **repository
secrets** for GitHub Actions (Settings → Secrets and variables → Actions):

| Channel | Variables |
|---|---|
| **ntfy** (easiest phone push; free, no account) | `NTFY_TOPIC` = a long, hard-to-guess name such as `ps5pro-k3j9x7q2`. Install the ntfy app and subscribe to that topic. |
| Discord | `DISCORD_WEBHOOK_URL` |
| Slack | `SLACK_WEBHOOK_URL` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Pushover | `PUSHOVER_TOKEN`, `PUSHOVER_USER` |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (Gmail: `smtp.gmail.com`, port 587, with an [app password](https://myaccount.google.com/apppasswords)) |

Test them with `python -m ps5_pro_tracker --test-alert`, or in GitHub run the
workflow manually with **"Only send a test notification"** ticked.

**Best Buy (recommended):** get a free key at <https://developer.bestbuy.com>
and set `BESTBUY_API_KEY`. The official API isn't blocked like the website is.

## Option A: run it on GitHub Actions (nothing to keep running)

1. Push this repo to GitHub. The workflow in `.github/workflows/tracker.yml`
   runs every 10 minutes.
2. Add at least one alert secret (e.g. `NTFY_TOPIC`), plus `BESTBUY_API_KEY`
   if you have one.
3. Actions tab → *PS5 Pro stock tracker* → **Run workflow** to try it.

Notes:
- Retailers such as Walmart, Amazon and Target often block traffic from cloud
  servers, including GitHub's. Those sites show 🟠 BLOCKED in the run summary.
  For the best coverage, also run it from a home computer (Option B).
- GitHub pauses scheduled workflows after 60 days with no repo activity. Any
  commit restarts them.
- In a **public** repo Actions minutes are free. In a private repo a run every
  10 minutes uses roughly 9,000–13,000 minutes a month, more than the free 2,000.
  Change the cron to `*/30 * * * *` or make the repo public.

## Option B: run it on your own computer

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

export NTFY_TOPIC=ps5pro-k3j9x7q2          # plus BESTBUY_API_KEY etc.
python -m ps5_pro_tracker --test-alert      # check your phone
python -m ps5_pro_tracker --loop --interval 300   # check every ~5 minutes
```

Other options:

```
--only Walmart Target   check only these retailers
--dry-run               check but don't send alerts
--no-browser            never start the headless browser (faster, more blocking)
-c retailers.yaml       config file
-s state/state.json     state file
-v                      debug logging
```

Please keep the interval at 5 minutes or longer. Hitting stores more often
gets your IP blocked and won't make you faster than retailers' own
"notify me" emails.

## Customizing `retailers.yaml`

- **Add a known listing:** paste its URL under that retailer's `products:`.
- **Add a retailer:** add an entry with `name`, a `search:` URL, and
  `render: true` if its pages need JavaScript.
- **Price limits:** `settings.max_price` / `min_price`.
- **What counts as a PS5 Pro:** the `match.include` / `match.exclude` regexes.

## Tests

```bash
pip install pytest && python -m pytest
```

## Limits

- Store websites change. If a retailer starts showing `UNKNOWN` all the time,
  its page layout has probably changed. Open an issue or adjust the config.
- Some drops (PlayStation Direct queues, Walmart+ early access) are
  invite-only or behind a waiting room. The tracker tells you the page is
  live, but you still have to check out yourself. It never adds to cart or buys.
