# MadadPass

MadadPass helps families request community grocery assistance when complex forms, missing information, language barriers, or weak internet make it hard to apply. This prototype uses fictional households, policies, vouchers, and payments.

## Start on Windows

1. Extract the entire ZIP into a folder. Keep the project files together.
2. Double-click `Start-MadadPass.cmd`. The launcher uses Python 3.11 or newer and installs the three listed runtime dependencies in a local virtual environment if needed.
3. Open `http://127.0.0.1:8765` if the browser does not open automatically. Pick a demo role on the sign-in screen.

For a terminal start, run `python -m pip install -r requirements.txt` once, then `python run.py --no-browser`. Stop the server with Ctrl+C. For an independent demo instance, use `python run.py --port 8766 --db data/fresh.sqlite3`. SQLite runs the included migration and loads fictional seed records on first launch. The application creates `data/` for the database and signing secret. Keep that directory private if you share the code.

## Try the full workflow

1. Choose **Citizen** and open **Apply for help**. Try the Urdu example and choose **Suggest details**. Review the suggested household size and income, use `HH-201` and a fictional address, and confirm before submitting.
2. Upload the downloadable sample income statement from the new application. It contains five members and PKR 30,000 monthly income. Open the original alongside the extracted fields.
3. Sign out and choose **Review officer**. Open the new case, read the assessment and policy sources, assign it to yourself if needed, and approve it with a verified-evidence checkbox and an explanation.
4. Return to **Citizen**, open the approved case, and choose **Show voucher QR**. Open **Merchant** in a separate browser profile, or copy the token and switch roles. Scan or paste the token to redeem. Submitting the same token again starts a new attempt and returns an already-redeemed result. The merchant history has one receipt and one simulated settlement.
5. Open the two seeded Muhammad/Mohammad cases as an officer to see duplicate candidates. Open Zoya's case to see missing income and evidence. The seeded Amina case starts with evidence and can be used to demonstrate approval immediately.
6. From **Administrator**, preview a lower income ceiling against open cases, then publish it. An officer must reassess open cases using the current policy before deciding. Closed cases retain the policy referenced by their recorded decision. **Auditor** can open a redacted assessment trace from an event's case link.
7. As **Citizen**, withdraw Sana Yusuf's seeded consent from **Sharing access**. Switch to **Community helper** and verify that Amina's cases are no longer listed. Grant a narrower scope or restore application access from the citizen account.

The sign-in shortcuts use the usernames `citizen`, `helper`, `officer`, `merchant`, `admin`, and `auditor`, with password `MadadDemo26!`. Other fictional logins include `citizen-b`, `citizen-c`, `citizen-d`, `officer-cedar`, and `citizen-cedar`. Cedar accounts show the workspace boundary; the Harbor officer has no access to Cedar cases. Use a different local browser profile or sign out when switching accounts. The app binds to localhost because these are shared demonstration credentials.

## AI, accessibility, and uncertainty

| Feature | Local implementation | Failure or uncertainty behavior |
| --- | --- | --- |
| Structured intake | English and Urdu numeric field extraction with Unicode digit normalization and JSON Schema validation | Missing or contradictory values remain empty. The citizen checks editable suggestions. |
| Evidence | Template-aware, validated text-document extraction. Optional Gemini extraction for fictional JPEG/PNG files. | Image uploads remain available for manual checking when cloud extraction is disabled or unavailable. Conflicts route to human review. |
| Duplicate routing | Normalized name/address similarity and exact synthetic-reference matching | Routes to a person at name similarity ≥ 0.86 and address similarity ≥ 0.78, or exact fictional reference. Scores measure string similarity. No automatic merge or rejection. |
| Policy retrieval | TF–IDF-ranked excerpts indexed from versioned fictional program rules | Reports the rule identifier and version. A deterministic rules engine supplies the recommendation. |
| Relief message screening | Detects common requests for codes, advance fees, shortened links, and urgent payment wording | Displays the matching signals and advises verification. A message without a matching pattern remains unverified. |
| Voice and offline | Opt-in Urdu browser dictation, optional Groq speech-to-text, Urdu playback when a device voice exists, IndexedDB drafts and a cached app shell | Typed entry and a bilingual example always remain available. A confirmed new application can wait for reconnection and retry with the same reference. Documents and credentials are not included in offline drafts. |

Check policy versions using the auditor trace or the case's sources. Officers must review every approval. Required fields and rule limits block approval when missing or unmet. An unresolved duplicate or evidence warning requires the officer to verify the case and explain the resolution. Scanning depends on the browser and device. Merchants can always paste the displayed token.

### Optional cloud providers

Copy `.env.example` to `.env` and fill in the desired keys. Restart the server. **Use only fictional text, audio, and images.** The browser asks before sending synthetic material to a provider. Provider keys stay server-side. Cloud providers and device speech availability are independent of the local demo.

- [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Groq speech to text](https://console.groq.com/docs/speech-to-text)

## API and storage

The local `/docs` page is an interactive API explorer. `/openapi.json` provides an importable 3.1 specification, and `/health` reports database and integration availability. Sign in via `/api/login` for a JWT. Create or revoke a scoped `X-API-Key` under **Mock API keys**. The mock endpoints are `/mock/identity/{id}`, `/mock/data/{id}`, and `/mock/payment/settle`.

The project uses a Python Starlette/Uvicorn API, a lightweight same-origin browser interface, and SQLite with versioned SQL migrations. This local stack supports running from a Windows drive with no separate Node build, external database, hosted identity, or paid integration. Password hashes use scrypt. Session revocation, workspace and role checks, private evidence, and helper consent are enforced by the server. User-facing case history keeps inspector reasons. Auditor event metadata excludes names, addresses, original files, transcripts, raw credentials, and voucher tokens.

A voucher embeds an opaque token derived from a secret and random voucher identifier. Its database record stores a token hash. The issued status, one redemption row, mocked settlement, and audit record are changed atomically. A retry of the original request returns the existing receipt. A different request using the spent token is rejected. No cash changes hands. Document extraction can flag missing or conflicting information and does not verify authenticity. No calibrated accuracy, confidence, or false-alarm rates are claimed.

## Verify

From the project folder, run `python -m unittest discover -s tests -v`. The suite uses temporary databases and the actual HTTP routes. The audit panel and server logs show state transitions, status codes, and route timing. The local project package is suitable for the live demo. Public hosting, repository publication, video upload, and event submission require separate user-controlled accounts and actions.
