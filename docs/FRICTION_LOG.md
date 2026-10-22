# Pantry Pilot – Friction Log

Build, Ship, Shape: Amazon Developer Hackathon · Alexa+ track
Services: Amazon Bedrock (Nova 2 Lite), Alexa+ MCP add-ons, Amazon Cognito, AWS Lambda, Amazon Polly, Amazon Transcribe, AWS CDK

Each entry lists the task, the steps we took, expected vs. actual result, severity, our workaround, and a suggestion.

---

## 1. Bedrock quota on a new account

| | |
|---|---|
| **Task** | Call Amazon Nova 2 Lite from a new AWS account |
| **Steps** | Enabled model access in the Bedrock console → called the Converse API from Lambda |
| **Expected** | A model response |
| **Actual** | `ThrottlingException: Too many tokens per day`: the account's daily token quota was 0, but the error reads like a rate limit |
| **Severity** | 🔴 High – blocked every AI feature |
| **Workaround** | Called Nova through a second account while waiting for a quota increase |
| **Suggestion** | Return a clear error ("your quota is 0 – request an increase here") and fast-track quotas for hackathon participants |

## 2. Registering an MCP server with Alexa+

| | |
|---|---|
| **Task** | Connect our self-hosted MCP server to Alexa+ as an add-on |
| **Steps** | Read the Alexa+ MCP toolkit docs → built OAuth 2.1 account linking on Cognito → looked for a registration path |
| **Expected** | A way for entrants to register and test on a device |
| **Actual** | Docs say "select partners only"; examples are en-US; Alexa+ is in Early Access in India |
| **Severity** | 🔴 High – couldn't test on real Alexa+ |
| **Workaround** | Built an Alexa+ web simulator that calls the same MCP server and tools; proved interoperability by connecting the same server to another MCP client as a custom connector |
| **Suggestion** | A hackathon sandbox, or a test console that calls your MCP server exactly like Alexa+ does |

## 3. MCP OAuth on Amazon Cognito ("just the URL" connect)

| | |
|---|---|
| **Task** | Let MCP clients connect with only `https://trypantrypilot.com/mcp` |
| **Steps** | Cognito user pool + resource server + app clients → added the URL as a custom MCP connector |
| **Expected** | Automatic client registration and a passwordless sign-in matching our app |
| **Actual** | No Dynamic Client Registration ("Couldn't register with sign-in service"); the hosted UI only offers email + password, while our app uses email codes |
| **Severity** | 🔴 High – connectors failed or showed an off-brand password box |
| **Workaround** | Built `/oauth/register` (RFC 7591), where Pantry Pilot registers MCP clients itself, and routed `/oauth/authorize` to our own branded email-code sign-in with a consent screen. The first version only accepted public clients; a client registering with `client_secret_post` was rejected until we added confidential clients |
| **Suggestion** | DCR support (or a documented reference pattern) and email OTP in Cognito managed login, with a worked MCP / Alexa+ account-linking example |

## 4. 401 response from the MCP endpoint

| | |
|---|---|
| **Task** | Return a spec-compliant 401 from `/mcp` |
| **Steps** | Followed the MCP authorization spec |
| **Expected** | Send `WWW-Authenticate` with `resource_metadata` |
| **Actual** | We read that Alexa+ doesn't support that header, so we omit it and rely on `/.well-known/oauth-protected-resource/mcp` discovery |
| **Severity** | 🟡 Medium |
| **Workaround** | Path-based discovery documents only |
| **Suggestion** | Document exactly what Alexa+ expects on a 401 from an MCP server |

## 5. Nova meal-planning and extraction quality

| | |
|---|---|
| **Task** | Plan dinners in the household's cuisine (Bengali) from the real pantry; read grocery bills and photos |
| **Steps** | Converse API with forced tool output; prompt with pantry, expiry, cuisine, diet and language |
| **Expected** | Real dishes whose names match their recipes; nothing extracted from non-food images |
| **Actual** | Names that didn't match recipes (an egg-and-cheddar scramble called "Chingri Malai", a prawn curry), "Bengali-style pasta", and plausible groceries returned for images with no food |
| **Severity** | 🟡 Medium – visible to users and judges |
| **Workaround** | A strict second Nova pass checks every dish name against its ingredients and steps and replaces misleading names; dishes whose main ingredient is listed as missing, or isn't stocked while the dish uses nothing from the pantry, are dropped; per-item confidence and a human review screen for scans |
| **Suggestion** | Recommended prompt patterns for "nothing found" in extraction, and for keeping generated names consistent with generated content |

## 6. Tool use in a multi-turn agent

| | |
|---|---|
| **Task** | Always call `plan_meals` when the user asks about a meal |
| **Steps** | Nova tool-calling loop with chat history |
| **Expected** | A fresh `plan_meals` call per meal question |
| **Actual** | The model sometimes replayed an earlier answer from history without calling the tool (stale dishes, wrong "missing" items) |
| **Severity** | 🟡 Medium |
| **Workaround** | Force the `plan_meals` tool on the first step for meal questions; save language changes ("talk to me in Bengali") before the agent runs |
| **Suggestion** | Guidance and examples for per-turn forced tool choice in Converse-based agents |

## 7. Latency for voice

| | |
|---|---|
| **Task** | Voice-speed answers for model-backed tools |
| **Steps** | `plan_meals` / bill scanning call Nova inside the MCP tool |
| **Expected** | ~500 ms voice response target |
| **Actual** | 4–10 s; one long turn timed out at the edge ("Server offline") |
| **Severity** | 🟡 Medium |
| **Workaround** | Heartbeat every 10 s on long turns; short spoken acknowledgements |
| **Suggestion** | Guidance for long-running MCP tools in Alexa+ (progress notifications, "I'll tell you when it's ready") |

## 8. Indian-language voices

| | |
|---|---|
| **Task** | Speak replies in the household's language |
| **Steps** | Language setting → Polly voice selection |
| **Expected** | Voices for Bengali, Tamil, Kannada |
| **Actual** | Polly covers English and Hindi (Kajal) for India; no Bengali, Tamil or Kannada voices |
| **Severity** | 🟢 Low |
| **Workaround** | Show text in those languages; speak English or Hindi (or the phone's own voice) |
| **Suggestion** | More Indian-language Polly voices |

---

## What worked well

- **Nova 2 Lite + Converse API:** forced tool output gave reliable structured JSON from photos, PDFs and video in one call.
- **MCP:** one set of tools served Alexa+ (via our simulator), our web app, an external MCP client and our tests.
- **Lambda Web Adapter + CDK + GitHub Actions OIDC:** push-to-deploy serverless with no stored keys.
- **Transcribe streaming and Polly (Kajal, neural en-IN and hi-IN voice):** natural, accurate Indian English and Hindi.
