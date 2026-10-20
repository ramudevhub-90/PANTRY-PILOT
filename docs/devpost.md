## Inspiration

Every week our fridge hides something until it's too late: a bag of spinach, half a tub of paneer, coriander gone limp. We order groceries from two or three quick-commerce apps (India's 10-minute delivery apps, like Instacart), nobody remembers what's already at home, and dinner gets decided by whatever we notice first.

Alexa+ can already set timers, keep a shopping list and find recipes. What it can't do is know what's actually in *your* kitchen and when it goes bad. Pantry Pilot gives it that knowledge.

## What it does

**Pantry Pilot is a kitchen agent for Alexa+.** Ask it things the way you'd ask a person:

- **"What can I make for dinner?"** It ranks recipes by how much food that's about to expire they rescue: "Palak paneer: the spinach and paneer expire tomorrow. You're missing cream."
- **"What's going off soon?"** It reads the pantry, soonest expiry first.
- **"We finished the onions" / "Used 200 g of rice."** Stock updates, with unit conversion (500 g + 1 kg = 1.5 kg).
- **"Add the missing ingredients to my list."** The grocery list fills itself from tonight's plan.

The pantry stays accurate without typing:

- **Snap your groceries, a bill, a PDF invoice or an order screenshot**, or record a 10-second **video** of your fridge. Amazon Nova reads the items, pack sizes and quantities, skips non-food lines, fees and freebies, and estimates shelf life. A review screen highlights anything it's unsure of, so you fix quantities before saving.
- **Sync your Swiggy Instamart and Zepto orders.** Pantry Pilot reads your delivered orders through the apps' own MCP servers, skips cancelled orders and non-food items (yes, a razor and a nebulizer showed up in our grocery history), and lists the new groceries to confirm.
- **In the US:** search Kroger and add the products you approve to your own Kroger cart.

Pantry Pilot never places or pays for an order. It reads order history and fills carts only after you say yes; you check out in the store's app.

## How we built it

- **MCP server (Python, MCP SDK, protocol 2026-07-28 over Streamable HTTP)** with tools including `get_inventory`, `update_inventory`, `scan_pantry`, `plan_meals`, `build_grocery_list`, `get_grocery_list`, `set_timer`, `sync_grocery_orders`, `find_kroger_products` and `add_to_kroger_cart` (plus an Instacart shopping-list tool waiting on an Instacart API key). It's secured with OAuth 2.1 (Amazon Cognito) so Alexa+ can link a household's account, and our own web assistant calls the very same tools.
- **Amazon Nova 2 Lite on Amazon Bedrock** drives the assistant's tool-calling loop and reads photos, PDF bills and video through the Converse API, with forced tool output for structured, validated item lists and a confidence score per item.
- **Pantry Pilot is also an MCP client:** it connects to the official **Swiggy Instamart and Zepto MCP servers** with each user's own OAuth sign-in and reads order history with read-only tools.
- **Web app (React + TypeScript)** that simulates the Alexa+ experience: an Echo-style light ring, hands-free "Alexa, …" in any browser (the microphone streams to **Amazon Transcribe** over a WebSocket signed by our server), spoken replies, a live trace of every MCP tool call, and a kitchen dashboard (tonight's plan, pantry with expiry badges, grocery list, timers, connected shopping apps).
- **Serverless on AWS, deployed with CDK:** AWS Lambda (Lambda Web Adapter), Amazon CloudFront + S3, Amazon DynamoDB, Amazon Cognito (passwordless email-code sign-in via custom auth triggers), Amazon SES, Route 53 and ACM. Live at trypantrypilot.com.

## Challenges we ran into

- **Which grocery apps can actually share orders?** We tested each app's MCP endpoint with real accounts. Swiggy Instamart and Zepto expose order history; BigBasket only has search and cart; Instacart's MCP builds lists but can't read history; Amazon's shopping MCP only accepts approved clients; Blinkit and Flipkart Minutes have none. So we built photo, bill and screenshot scanning for everyone else.
- **Quantities from a photo are guesses.** Instead of pretending otherwise, Nova returns a confidence per item and the review screen asks the user to check only the uncertain ones.
- **Real order data is messy:** cancelled orders, non-food items, promo vouchers listed as products, and pack sizes like "200 g x 2". The scanner and order sync filter and normalise all of it.
- **Name matching:** an early bug merged newly bought "Tomatoes" into "Crushed tomatoes" (the cans). New stock now only merges with the same product, while "we finished the spinach" still finds "Baby spinach".

## Accomplishments that we're proud of

- One agent that both **serves** MCP (to Alexa+) and **consumes** MCP (from Swiggy Instamart and Zepto): agents talking to agents to run a real kitchen.
- Many ways to update the pantry (voice, chat, photo, bill, PDF, video, order sync), all funnelled through two MCP tools.
- A safety-first design: read-only access to order history, carts filled only on a "yes", never a checkout, and every AI-detected quantity reviewable before it's saved.
- Deployed and usable today, with passwordless sign-in and per-household data.

## What we learned

- MCP makes integrations composable: the same tool definitions serve Alexa+, our web app and our tests, and other companies' MCP servers plug in as easily as our own.
- Multimodal models are excellent at reading bills and screenshots, but confidence scores and a human review step matter more than squeezing out extra accuracy.

## What's next

- **Price comparison across apps:** "Where is my list cheapest?" compares Swiggy Instamart, Zepto and BigBasket and fills the cheapest cart.
- **Food-waste stats:** how much food and money the household saved this month.
- **Hindi and English:** Alexa+ in India already switches languages mid-conversation, so Pantry Pilot will too ("Aaj dinner mein kya banaun?").
- **Fire TV, Ring and Bee:** recipes on the big screen; a delivery at the door prompting a bill scan.

## Built with

python · typescript · react · model-context-protocol · amazon-bedrock · amazon-nova · aws-lambda · amazon-dynamodb · amazon-cognito · amazon-cloudfront · amazon-s3 · amazon-ses · amazon-transcribe · aws-cdk · alexa-plus
