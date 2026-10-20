# Devpost extras (not published in the repo README)

## Product feedback (paste into the "feedback" field)

**Alexa+ MCP add-ons**
- What worked: building for Alexa+ as a plain MCP server meant one set of tools served Alexa+, our own web assistant and our tests. Tool annotations (`readOnlyHint`, `destructiveHint`) are a natural way to tell Alexa which actions need confirmation.
- Hard to find: whether hackathon entrants can actually register a self-hosted MCP server with the Alexa+ MCP Toolkit, and which locales are supported. The docs say "select partners only" and the examples are en-US, while Alexa+ is in Early Access in India. A clear "hackathon sandbox" path (or a test console that talks to your MCP server like Alexa+ would) would remove the biggest uncertainty for entrants.
- Account linking: OAuth 2.1 + PKCE without Dynamic Client Registration is reasonable, but a worked Cognito example (redirect URLs, scopes, the 401 behaviour without `WWW-Authenticate`) would save hours.
- Latency budget: the 500 ms target is tight for tools that call a model (e.g. reading a photo with Nova). Guidance on long-running tools (progress notifications, or "I'll tell you when it's done") would help agent builders.

**Amazon Bedrock / Nova**
- Nova 2 Lite's Converse API with forced tool output gave us reliable structured JSON from photos, PDF bills and video in one call. Video input in particular is a great fit for "pan across the fridge".
- New accounts on the free plan start with a Bedrock quota of 0 tokens/day and the error is a generic `ThrottlingException: Too many tokens per day`. A clearer message ("your account's quota is 0, request an increase here") and faster quota grants for hackathon participants would help a lot; we had to call Nova through a second account in the meantime.
- On an image with no food at all, Nova still returned plausible groceries. A recommended prompt pattern or a built-in "nothing found" signal for extraction tasks would be valuable.

## Demo video script (under 3 minutes, English)

Record on the live site (trypantrypilot.com) on a phone or laptop, plus the Echo Show if Alexa+ is connected. Keep each shot short; total ≈ 2:45.

1. **0:00–0:15 Hook.** Close-up of a fridge with limp spinach. Voice-over: "Every week we throw away food we forgot we had. Alexa+ can set timers and find recipes, but it doesn't know what's in *my* kitchen. Pantry Pilot fixes that."
2. **0:15–0:40 Fill the pantry without typing.** Tap the camera, record a 10-second fridge video (or snap a grocery bill). Show the review screen: items, quantities, expiry, "2 to check". Fix one quantity, Save. Voice-over: "Amazon Nova reads photos, bills and video. Anything it's unsure of, I check before it's saved."
3. **0:40–1:05 Orders sync themselves.** Shopping apps card → Sync Zepto / Swiggy Instamart. New groceries appear. Voice-over: "In India we buy groceries from 10-minute delivery apps like Zepto and Swiggy Instamart. Pantry Pilot reads my orders through their official MCP servers, skips cancelled orders and non-food items, and lists the new groceries for me to confirm."
4. **1:05–1:45 Ask Alexa.** "What can I make for dinner?" Show the MCP tool-call trace (`get_inventory`, `plan_meals`) and the answer that uses the expiring items. Then "Add the missing ingredients to my list" and "We finished the onions."
5. **1:45–2:10 Under the hood.** Simple architecture slide: Alexa+ → MCP server on AWS Lambda → DynamoDB, Amazon Nova on Bedrock; Pantry Pilot → Swiggy / Zepto MCP servers. Voice-over: "Pantry Pilot is an MCP server for Alexa+, and an MCP client of the grocery apps: agents talking to agents."
6. **2:10–2:30 Works in the US too.** "Add milk and eggs to my Kroger cart" → products read back → "Yes" → added. Voice-over: "It never checks out or pays. You approve every item and pay in the store's app."
7. **2:30–2:45 Close.** Dashboard with "expiring soon" empty. Voice-over: "Pantry Pilot: Alexa+ finally knows what's in your kitchen." Show trypantrypilot.com and the GitHub URL.
