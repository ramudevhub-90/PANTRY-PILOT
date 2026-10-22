# Pantry Pilot

**A kitchen agent for Alexa+ that knows what's in your kitchen.** It tracks what food you have and when it expires, plans dinner around what's about to go bad, and keeps the pantry up to date from your grocery orders, bills and photos.

Pantry Pilot is an **MCP server** (Model Context Protocol, Streamable HTTP) that Alexa+ connects to, backed by **Amazon Nova 2 Lite on Amazon Bedrock**. It is also an **MCP client** of the official Swiggy Instamart, Zepto and BigBasket MCP servers: it reads order history, compares prices and places orders you confirm. You can also chat with it on **WhatsApp**, and add it to **Claude** as a custom connector.

- Live app: https://trypantrypilot.com
- MCP endpoint: `https://trypantrypilot.com/mcp` (OAuth 2.1 via Amazon Cognito)

Built for the [Build, Ship, Shape: Amazon Developer Hackathon](https://amazonappdev2026.devpost.com/), Alexa+ track.

## What it does

| Ask | What happens |
| --- | --- |
| "What should I cook?" | Nova suggests dishes for the time of day, your city, home cuisine and diet, using food that expires first; confirms the dish, then lists what's missing. |
| "What's expiring soon?" | Reads the pantry, soonest expiry first. |
| "We finished the onions" / "Used 200 g of rice" | Updates stock, converting units (500 g + 1 kg = 1.5 kg). |
| "Add cream and ginger to the list" | Adds to the grocery list, or adds a planned meal's missing ingredients. |
| Photo, bill, PDF, order screenshot or a 10-second fridge video | Nova lists the food items with quantities, pack sizes, shelf life and a confidence score. Nothing is saved until you review it. |
| "Sync my grocery orders" | Reads recent Swiggy Instamart and Zepto orders, skips cancelled orders and non-food items, and lists the new groceries for you to confirm. |
| Hands-free: "Alexa, what's expiring?" | In any browser, the web simulator streams the microphone to Amazon Transcribe and answers whenever it hears "Alexa" (switches itself off after 5 quiet minutes). |
| "Where is my list cheapest?" | Live prices on Swiggy Instamart and Zepto at your saved address. |
| "Order it from Zepto" … "Place the order" | Fills the cart and reads back items, total, address and payment; places it only after you say "place the order" in a later turn. Cash on delivery by default; payment links by email and WhatsApp. |
| WhatsApp: message, photo or bill | Same agent on WhatsApp; photos and PDF bills are read by Nova with an "Add to pantry" button; "scan" opens the app's camera. |
| Any language | Replies in the language you speak (English, Hindi, Bengali…); Amazon Polly speaks the answer. |
| US: "Add milk to my Kroger cart" | Finds Kroger products and adds them to your own Kroger cart after you say yes. |

**Ordering needs your explicit confirmation.** The order is read back first and placed only when you say "place the order" in a later turn; the total is re-checked before checkout ([server/pantry_pilot/ordering.py](server/pantry_pilot/ordering.py)). Kroger carts are filled after you say yes; you check out in Kroger's app.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `get_inventory` | Pantry items with quantities and days left (read-only) |
| `update_inventory` | Add, use, remove or correct stock |
| `scan_pantry` | Read photos, video or a PDF bill with Amazon Nova (read-only; returns detections) |
| `plan_meals` | Rank recipes by expiring food rescued |
| `build_grocery_list` / `get_grocery_list` | Manage the grocery list |
| `set_timer` | Kitchen timer shown on the dashboard |
| `sync_grocery_orders` | Read new Swiggy Instamart and Zepto orders as grocery items to confirm |
| `compare_grocery_prices` | Compare the list's prices on the connected apps (read-only) |
| `prepare_order` / `place_order` | Fill a Zepto or Swiggy Instamart cart and read it back; place it after "place the order" |
| `set_food_preferences` | Diet, cuisines, home cuisine, city, reply language, shopping region |
| `remove_from_grocery_list` / `cancel_timer` | Undo list items and timers |
| `shop_on_instacart` | Turn the grocery list into an Instacart shopping-list page (needs an Instacart Developer Platform key; not enabled on the live demo) |
| `find_kroger_products` / `add_to_kroger_cart` | Search Kroger and fill the user's Kroger cart |

## Architecture

```
Alexa+ ──MCP (Streamable HTTP, OAuth)──┐
                                       ▼
Web app (React) ──/api──► CloudFront ──► Lambda (Python, Starlette + MCP SDK)
  S3-hosted                               │   ├─► DynamoDB (pantry, list, tokens)
                                          │   ├─► Amazon Bedrock: Nova 2 Lite (agent + vision)
                                          │   ├─► Swiggy Instamart / Zepto MCP servers (order history)
                                          │   ├─► Amazon Transcribe (hands-free voice) and Polly (spoken replies)
                                          │   └─► WhatsApp via AWS End User Messaging Social (order updates)
WhatsApp ─► End User Messaging ─► SNS ─► SQS ─► inbox Lambda (same server; chat, photo scans)
                                          └── Amazon Cognito (web sign-in, Alexa account linking)
```

- **server/**: Python MCP server and web API (`pantry_pilot/mcp_server.py` has the core tools, `nova.py` the Bedrock calls, `connectors.py` the Swiggy/Zepto MCP client, `agent.py` the Nova tool-calling loop used by the web assistant and WhatsApp, `ordering.py` price comparison and confirmed ordering, `whatsapp.py` the WhatsApp chat).
- **web/**: React + TypeScript app that simulates the Alexa+ experience (voice in, spoken replies, a live trace of every MCP tool call) next to a kitchen dashboard.
- **infra/**: AWS CDK stack: Lambda (with Lambda Web Adapter), CloudFront, S3, DynamoDB, Cognito (email-code sign-in via custom auth triggers), SES, SQS, Route 53, ACM. GitHub Actions deploys every push to `main` (OIDC, no stored AWS keys).

## Run locally

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/) or pip, Node.js 20+.

```bash
# Server (NOVA_MODE=mock returns sample detections without AWS)
cd server
uv sync
NOVA_MODE=mock PANTRY_DATA_DIR=.data uv run uvicorn --factory pantry_pilot.app:create_app --port 8787

# Web app, in a second terminal
cd web
npm install
VITE_API_URL=http://localhost:8787 npm run dev
```

With AWS credentials that can call Bedrock, drop `NOVA_MODE=mock` to use Amazon Nova.

Tests: `cd server && uv run pytest`

## Deploy

```bash
bash scripts/build-lambda.sh          # package the server for Lambda (arm64, Python 3.13)
cd infra && npm install
npx cdk deploy                        # optional: -c domain=<your domain> -c nova=mock
```

The stack outputs the app URL, the MCP URL and the Cognito client IDs. For Alexa+, register the `AlexaClientId` client's redirect URLs with `-c alexaCallbacks=<comma-separated URLs>`.

## License

[MIT](LICENSE)
