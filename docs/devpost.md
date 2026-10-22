## Inspiration

Every week our fridge hides something until it's too late: a bag of spinach, half a tub of paneer, coriander gone limp. We order groceries from two or three quick-commerce apps (India's 10-minute delivery apps, like Instacart), nobody remembers what's already at home, and dinner gets decided by whatever we notice first.

Alexa+ can already set timers, keep a shopping list and find recipes. What it can't do is know what's actually in *your* kitchen and when it goes bad. Pantry Pilot gives it that knowledge.

## What it does

**Pantry Pilot is a kitchen agent for Alexa+.** Ask it things the way you'd ask a person, in English, Hindi or your own language. It answers in the language you speak.

- **"What should I cook?"** Amazon Nova suggests dishes for the time of day (breakfast, lunch, evening snacks, dinner) from what's in your pantry, putting the food that expires first to use. Suggestions fit your city, your home cuisine and your diet: a Bengali family in Bengaluru gets different lunches from one in Kolkata. Pantry Pilot confirms the dish with you before building the shopping list.
- **"What's going off soon?"** It reads the pantry, soonest expiry first.
- **"We finished the onions" / "Used 200 g of rice."** Stock updates, with unit conversion (500 g + 1 kg = 1.5 kg).
- **"Where is my list cheapest?"** It checks live prices on Swiggy Instamart and Zepto at your saved address and tells you which app has everything for less.
- **"Order it from Zepto."** It fills the cart and reads back the items, total, delivery address and payment method. It only places the order when you then say "place the order". Cash on delivery is the default; for card or UPI, the payment link arrives by email and WhatsApp.

The pantry stays accurate without typing:

- **Snap your groceries, a bill, a PDF invoice or an order screenshot**, or record a 10-second **video** of your fridge. Amazon Nova reads the items, pack sizes and quantities, skips non-food lines, fees and freebies, and estimates shelf life. A review screen highlights anything it's unsure of, so you fix quantities before saving.
- **Sync your Swiggy Instamart and Zepto orders.** Pantry Pilot reads your delivered orders through the apps' own MCP servers, skips cancelled orders and non-food items (yes, a razor and a nebulizer showed up in our grocery history), and lists the new groceries to confirm.

**WhatsApp, for when you're not near Alexa:**

- **Chat with Pantry Pilot on WhatsApp.** The same agent and conversation as Alexa: "what's expiring?", "what should I cook tonight?", "compare prices for my list".
- **Send a photo of your shopping or the bill.** Nova reads it, lists the items, and an "Add to pantry" button saves them.
- **"Scan"** replies with a button that opens the camera in the Pantry Pilot app.
- **Order confirmations and payment links** arrive on WhatsApp.

**It follows your region.** In India it works with Swiggy Instamart, Zepto and BigBasket. In the US it searches Kroger and fills your own Kroger cart (Instacart shopping lists are built and waiting on an Instacart API key).

**It's also a Claude connector.** Add trypantrypilot.com/mcp as a custom connector in Claude and the same tools work there, with the same email-code sign-in.

## How we built it

- **MCP server (Python, MCP SDK, Streamable HTTP)** with tools including `get_inventory`, `update_inventory`, `scan_pantry`, `plan_meals`, `set_food_preferences`, `build_grocery_list`, `get_grocery_list`, `set_timer`, `sync_grocery_orders`, `compare_grocery_prices`, `prepare_order`, `place_order`, `find_kroger_products` and `add_to_kroger_cart`. It's secured with OAuth 2.1 through Amazon Cognito: Alexa+ links a household's account, and Claude registers itself with Dynamic Client Registration. Our web assistant and the WhatsApp chat call the very same tools.
- **Amazon Nova 2 Lite on Amazon Bedrock** drives the tool-calling loop. Through the Converse API it also reads photos, PDF bills and video, writes the meal suggestions, and reads the shopping apps' results, using forced tool output for structured, validated JSON. A second, strict Nova pass checks that every dish name really matches its recipe.
- **Pantry Pilot is also an MCP client.** It connects to the official **Swiggy Instamart, Zepto and BigBasket MCP servers** with each user's own OAuth sign-in. It reads order history, compares prices and fills carts.
- **Safe ordering.** The order is read back in one turn and placed only in a later turn, after the user says "place the order", within 5 minutes. The live total is re-checked before checkout, and the cart and address IDs must match what was read back.
- **Voice.** **Amazon Transcribe** streaming gives hands-free "Alexa, …" in any browser, with Indian English and Hindi detected automatically. **Amazon Polly** (the Kajal neural voice) speaks the replies.
- **WhatsApp through AWS End User Messaging Social.** Incoming messages flow through Amazon SNS and Amazon SQS to a Lambda copy of the same server. It answers with free-form messages and reply buttons, downloads photos to a short-lived S3 bucket for Nova, and sends order updates with Meta-approved templates.
- **Web app (React + TypeScript)** simulates the Alexa+ experience: an Echo-style light ring, spoken replies, and a live trace of every MCP tool call. Next to it is a kitchen dashboard: the meal for now, the pantry with expiry badges, the grocery list, timers, food preferences and connected shopping apps.
- **Serverless on AWS, deployed with CDK and GitHub Actions** (OIDC, no stored AWS keys). It runs on AWS Lambda (Lambda Web Adapter), Amazon CloudFront and S3, Amazon DynamoDB, Amazon Cognito (passwordless email-code sign-in through custom auth triggers), Amazon SES, Route 53 and ACM. It's live at trypantrypilot.com.

## Challenges we ran into

- **Which grocery apps can actually share orders?** We tested each app's MCP endpoint with real accounts. Swiggy Instamart and Zepto expose order history. BigBasket only has search and cart. Instacart's MCP builds lists but can't read history. Amazon's shopping MCP only accepts approved clients, and Blinkit and Flipkart Minutes have none. So we built photo, bill and screenshot scanning for everyone else.
- **Letting an agent spend money.** "Yes" is too easy to mishear, so placing an order needs its own turn, the exact phrase "place the order", a fresh read-back and a re-checked total.
- **Meal ideas that feel like home.** Early suggestions kept offering frittatas and fajitas to an Indian family, and invented dish names. Nova now gets the local time, the city and the home cuisine, and a second Nova pass rejects names that don't match the recipe.
- **Quantities from a photo are guesses.** Instead of pretending otherwise, Nova returns a confidence per item, and the review screen asks the user to check only the uncertain ones.
- **Real order data is messy:** cancelled orders, non-food items, promo vouchers listed as products, and pack sizes like "200 g x 2". The scanner and order sync filter and normalise all of it.

## Accomplishments that we're proud of

- One agent that both **serves** MCP (to Alexa+ and Claude) and **consumes** MCP (from Swiggy Instamart, Zepto and BigBasket): agents talking to agents to run a real kitchen.
- Many ways to update the pantry (voice, chat, photo, bill, PDF, video, order sync, WhatsApp), all funnelled through the same MCP tools.
- Real ordering with a safety-first design: read-back, an explicit "place the order", cash on delivery by default, and confirmations by email and WhatsApp.
- Deployed and usable today, with passwordless sign-in, per-household data and replies in the user's own language.

## What we learned

- MCP makes integrations composable. The same tool definitions serve Alexa+, Claude, our web app, WhatsApp and our tests, and other companies' MCP servers plug in as easily as our own.
- Multimodal models are excellent at reading bills and screenshots, but confidence scores and a human review step matter more than squeezing out extra accuracy.
- For an agent that acts on your behalf, the safety rules belong in code (turns, phrases, re-checks), not only in the prompt.

## What's next

- **Food-waste stats:** how much food and money the household saved this month.
- **BigBasket in price comparison**, and smarter product matching (the right pack size, fresh versus frozen).
- **Fire TV, Ring and Bee:** recipes on the big screen, and a delivery at the door prompting a bill scan.

## Built with

python · typescript · react · model-context-protocol · amazon-bedrock · amazon-nova · amazon-transcribe · amazon-polly · aws-end-user-messaging · whatsapp · aws-lambda · amazon-dynamodb · amazon-sqs · amazon-sns · amazon-cognito · amazon-cloudfront · amazon-s3 · amazon-ses · aws-cdk · github-actions · alexa-plus
