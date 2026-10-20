# Pantry Pilot

**A kitchen agent for Alexa+ that knows what's in your kitchen.** It tracks what food you have and when it expires, plans dinner around what's about to go bad, and keeps the pantry up to date from your grocery orders, bills and photos.

Pantry Pilot is an **MCP server** (Model Context Protocol, Streamable HTTP) that Alexa+ connects to, backed by **Amazon Nova 2 Lite on Amazon Bedrock**. It is also an **MCP client**: it reads order history from the official Swiggy Instamart and Zepto MCP servers.

- Live app: https://trypantrypilot.com
- MCP endpoint: `https://trypantrypilot.com/mcp` (OAuth 2.1 via Amazon Cognito)

Built for the [Build, Ship, Shape: Amazon Developer Hackathon](https://amazonappdev2026.devpost.com/), Alexa+ track.

## What it does

| Ask | What happens |
| --- | --- |
| "What can I make for dinner?" | Ranks recipes by how much food that's about to expire they use up, and lists what's missing. |
| "What's expiring soon?" | Reads the pantry, soonest expiry first. |
| "We finished the onions" / "Used 200 g of rice" | Updates stock, converting units (500 g + 1 kg = 1.5 kg). |
| "Add cream and ginger to the list" | Adds to the grocery list, or adds a planned meal's missing ingredients. |
| Photo, bill, PDF, order screenshot or a 10-second fridge video | Nova lists the food items with quantities, pack sizes, shelf life and a confidence score. Nothing is saved until you review it. |
| "Sync my grocery orders" | Reads recent Swiggy Instamart and Zepto orders, skips cancelled orders and non-food items, and lists the new groceries for you to confirm. |
| Hands-free: "Alexa, what's expiring?" | In any browser, the web simulator streams the microphone to Amazon Transcribe and answers whenever it hears "Alexa" (switches itself off after 5 quiet minutes). |
| US: "Add milk to my Kroger cart" | Finds Kroger products and adds them to your own Kroger cart after you say yes. |

**Pantry Pilot never places or pays for an order.** It only reads order history and fills carts after you confirm; you check out in the store's own app.

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
                                          │   └─► signs Amazon Transcribe streaming URLs (hands-free voice)
                                          └── Amazon Cognito (web sign-in, Alexa account linking)
```

- **server/**: Python MCP server and web API (`pantry_pilot/mcp_server.py` has the core tools, `nova.py` the Bedrock calls, `connectors.py` the Swiggy/Zepto MCP client, `agent.py` the Nova tool-calling loop used by the web assistant).
- **web/**: React + TypeScript app that simulates the Alexa+ experience (voice in, spoken replies, a live trace of every MCP tool call) next to a kitchen dashboard.
- **infra/**: AWS CDK stack: Lambda (with Lambda Web Adapter), CloudFront, S3, DynamoDB, Cognito (email-code sign-in via custom auth triggers), Route 53, ACM.

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
