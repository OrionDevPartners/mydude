"""Curated catalog of services for the vault, guided signup helper, and
connected-services page.

Each entry describes a service the user may want credentials for. A service can
support manual API keys (``key_url`` present) and/or a first-class Replit
integration / OAuth connector (``connector`` set to the connector slug).
"""

CATEGORIES = [
    "AI / LLM",
    "Payments",
    "Developer",
    "Communications",
    "Productivity",
    "Google",
    "Cloud",
    "Finance",
    "CRM",
    "Automation",
    "Other",
]

SERVICE_CATALOG = [
    # ---- AI / LLM ----
    {
        "slug": "openai",
        "name": "OpenAI",
        "category": "AI / LLM",
        "env_var": "OPENAI_API_KEY",
        "key_prefix": "sk-",
        "signup_url": "https://platform.openai.com/signup",
        "key_url": "https://platform.openai.com/api-keys",
        "connector": None,
        "steps": [
            "Create an account at platform.openai.com",
            "Open the API Keys page",
            "Click 'Create new secret key'",
            "Copy the key (starts with sk-) and paste it below",
        ],
    },
    {
        "slug": "hume",
        "name": "Hume AI (Emotion)",
        "category": "AI / LLM",
        "env_var": "HUME_API_KEY",
        "key_prefix": "",
        "signup_url": "https://platform.hume.ai/",
        "key_url": "https://platform.hume.ai/settings/keys",
        "connector": None,
        "steps": [
            "Create an account at platform.hume.ai",
            "Open Settings -> API Keys",
            "Create a new API key",
            "Copy the key and paste it below (powers mood / emotion capture)",
            "Note: Hume's Expression Measurement API sunsets 2026-06-14",
        ],
    },
    {
        "slug": "anthropic",
        "name": "Anthropic (Claude)",
        "category": "AI / LLM",
        "env_var": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
        "signup_url": "https://console.anthropic.com/",
        "key_url": "https://console.anthropic.com/settings/keys",
        "connector": None,
        "steps": [
            "Sign up at console.anthropic.com",
            "Go to Settings -> API Keys",
            "Click 'Create Key'",
            "Copy the key (starts with sk-ant-) and paste it below",
        ],
    },
    {
        "slug": "gemini",
        "name": "Google Gemini",
        "category": "AI / LLM",
        "env_var": "GEMINI_API_KEY",
        "key_prefix": "AIza",
        "signup_url": "https://aistudio.google.com/",
        "key_url": "https://aistudio.google.com/app/apikey",
        "connector": None,
        "steps": [
            "Sign in to Google AI Studio",
            "Open the 'Get API key' page",
            "Click 'Create API key'",
            "Copy the key and paste it below",
        ],
    },
    {
        "slug": "grok",
        "name": "xAI Grok",
        "category": "AI / LLM",
        "env_var": "GROK_API_KEY",
        "key_prefix": "xai-",
        "signup_url": "https://console.x.ai/",
        "key_url": "https://console.x.ai/",
        "connector": None,
        "steps": [
            "Sign up at console.x.ai",
            "Open the API Keys section",
            "Create a new key",
            "Copy the key (starts with xai-) and paste it below",
        ],
    },
    {
        "slug": "perplexity",
        "name": "Perplexity",
        "category": "AI / LLM",
        "env_var": "PERPLEXITY_API_KEY",
        "key_prefix": "pplx-",
        "signup_url": "https://www.perplexity.ai/",
        "key_url": "https://www.perplexity.ai/settings/api",
        "connector": None,
        "steps": [
            "Sign up at perplexity.ai",
            "Open Settings -> API",
            "Generate an API key",
            "Copy the key (starts with pplx-) and paste it below",
        ],
    },
    {
        "slug": "mistral",
        "name": "Mistral AI",
        "category": "AI / LLM",
        "env_var": "MISTRAL_API_KEY",
        "key_prefix": "",
        "signup_url": "https://console.mistral.ai/",
        "key_url": "https://console.mistral.ai/api-keys/",
        "connector": None,
        "steps": [
            "Sign up at console.mistral.ai",
            "Open the API Keys page",
            "Create a new key",
            "Copy the key and paste it below",
        ],
    },
    {
        "slug": "cohere",
        "name": "Cohere",
        "category": "AI / LLM",
        "env_var": "COHERE_API_KEY",
        "key_prefix": "",
        "signup_url": "https://dashboard.cohere.com/welcome/register",
        "key_url": "https://dashboard.cohere.com/api-keys",
        "connector": None,
        "steps": [
            "Sign up at dashboard.cohere.com",
            "Open the API Keys page",
            "Create or copy your key",
            "Paste it below",
        ],
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "category": "AI / LLM",
        "env_var": "OPENROUTER_API_KEY",
        "key_prefix": "sk-or-",
        "signup_url": "https://openrouter.ai/",
        "key_url": "https://openrouter.ai/keys",
        "connector": None,
        "steps": [
            "Sign in at openrouter.ai",
            "Open the Keys page",
            "Create a new key",
            "Copy it (starts with sk-or-) and paste it below",
        ],
    },
    {
        "slug": "huggingface",
        "name": "Hugging Face",
        "category": "AI / LLM",
        "env_var": "HUGGINGFACE_API_KEY",
        "key_prefix": "hf_",
        "signup_url": "https://huggingface.co/join",
        "key_url": "https://huggingface.co/settings/tokens",
        "connector": None,
        "steps": [
            "Sign up at huggingface.co",
            "Open Settings -> Access Tokens",
            "Create a new token",
            "Copy it (starts with hf_) and paste it below",
        ],
    },
    # ---- Payments ----
    {
        "slug": "stripe",
        "name": "Stripe",
        "category": "Payments",
        "env_var": "STRIPE_SECRET_KEY",
        "key_prefix": "sk_",
        "signup_url": "https://dashboard.stripe.com/register",
        "key_url": "https://dashboard.stripe.com/apikeys",
        "connector": "stripe",
        "steps": [
            "Create a Stripe account",
            "Open Developers -> API keys",
            "Reveal your secret key",
            "Copy it (starts with sk_) and paste it below",
        ],
    },
    # ---- Developer ----
    {
        "slug": "github",
        "name": "GitHub",
        "category": "Developer",
        "env_var": "GITHUB_TOKEN",
        "key_prefix": "ghp_",
        "signup_url": "https://github.com/join",
        "key_url": "https://github.com/settings/tokens",
        "connector": "github",
        "steps": [
            "Sign in to GitHub",
            "Go to Settings -> Developer settings -> Personal access tokens",
            "Generate a new token with the scopes you need",
            "Copy it and paste it below",
        ],
    },
    # ---- Communications ----
    {
        "slug": "twilio",
        "name": "Twilio",
        "category": "Communications",
        "env_var": "TWILIO_AUTH_TOKEN",
        "key_prefix": "",
        "signup_url": "https://www.twilio.com/try-twilio",
        "key_url": "https://console.twilio.com/",
        "connector": "twilio",
        "steps": [
            "Sign up at twilio.com",
            "Open the Console dashboard",
            "Copy your Auth Token",
            "Paste it below",
        ],
    },
    {
        "slug": "sendgrid",
        "name": "SendGrid",
        "category": "Communications",
        "env_var": "SENDGRID_API_KEY",
        "key_prefix": "SG.",
        "signup_url": "https://signup.sendgrid.com/",
        "key_url": "https://app.sendgrid.com/settings/api_keys",
        "connector": "sendgrid",
        "steps": [
            "Sign up at sendgrid.com",
            "Open Settings -> API Keys",
            "Create an API key",
            "Copy it (starts with SG.) and paste it below",
        ],
    },
    {
        "slug": "resend",
        "name": "Resend",
        "category": "Communications",
        "env_var": "RESEND_API_KEY",
        "key_prefix": "re_",
        "signup_url": "https://resend.com/signup",
        "key_url": "https://resend.com/api-keys",
        "connector": None,
        "steps": [
            "Sign up at resend.com",
            "Open the API Keys page",
            "Create an API key",
            "Copy it (starts with re_) and paste it below",
        ],
    },
    {
        "slug": "slack",
        "name": "Slack",
        "category": "Communications",
        "env_var": "SLACK_BOT_TOKEN",
        "key_prefix": "xoxb-",
        "signup_url": "https://slack.com/get-started",
        "key_url": "https://api.slack.com/apps",
        "connector": "slack",
        "steps": [
            "Create a Slack app at api.slack.com/apps",
            "Add the bot token scopes you need",
            "Install the app to your workspace",
            "Copy the Bot User OAuth Token (starts with xoxb-) and paste it below",
        ],
    },
    # ---- Productivity ----
    {
        "slug": "notion",
        "name": "Notion",
        "category": "Productivity",
        "env_var": "NOTION_API_KEY",
        "key_prefix": "secret_",
        "signup_url": "https://www.notion.so/signup",
        "key_url": "https://www.notion.so/my-integrations",
        "connector": "notion",
        "steps": [
            "Sign in to Notion",
            "Open notion.so/my-integrations",
            "Create a new internal integration",
            "Copy the Internal Integration Secret and paste it below",
        ],
    },
    # ---- Finance / Accounting ----
    {
        "slug": "quickbooks",
        "name": "QuickBooks Online",
        "category": "Finance",
        "env_var": "QUICKBOOKS_ACCESS_TOKEN",
        "key_prefix": "",
        "signup_url": "https://quickbooks.intuit.com/",
        "key_url": "https://developer.intuit.com/app/developer/myapps",
        "connector": "quickbooks",
        "steps": [
            "Sign in to the Intuit Developer portal",
            "Open your app and the OAuth Playground (or your token flow)",
            "Generate an access token for your company (realm)",
            "Paste the access token below, then add QUICKBOOKS_REALM_ID as a second key",
        ],
    },
    {
        "slug": "plaid",
        "name": "Plaid",
        "category": "Finance",
        "env_var": "PLAID_ACCESS_TOKEN",
        "key_prefix": "access-",
        "signup_url": "https://dashboard.plaid.com/signup",
        "key_url": "https://dashboard.plaid.com/team/keys",
        "connector": "plaid",
        "steps": [
            "Sign up at dashboard.plaid.com",
            "Open Team Settings -> Keys to find your client_id and secret",
            "Link an Item and exchange its public token for an access token",
            "Paste the access token below, then add PLAID_CLIENT_ID and PLAID_SECRET",
        ],
    },
    # ---- Google (OAuth connectors) ----
    {
        "slug": "google-mail",
        "name": "Gmail",
        "category": "Google",
        "env_var": None,
        "key_prefix": "",
        "signup_url": "https://mail.google.com/",
        "key_url": None,
        "connector": "google-mail",
        "steps": [],
        "description": "Lets MyDude read one-time login / verification codes from your email, so email-OTP logins don't silently fall back to needing you.",
    },
    {
        "slug": "google-sheet",
        "name": "Google Sheets",
        "category": "Google",
        "env_var": None,
        "key_prefix": "",
        "signup_url": "https://sheets.google.com/",
        "key_url": None,
        "connector": "google-sheet",
        "steps": [],
    },
    {
        "slug": "google-calendar",
        "name": "Google Calendar",
        "category": "Google",
        "env_var": None,
        "key_prefix": "",
        "signup_url": "https://calendar.google.com/",
        "key_url": None,
        "connector": "google-calendar",
        "steps": [],
    },
    {
        "slug": "google-docs",
        "name": "Google Docs",
        "category": "Google",
        "env_var": None,
        "key_prefix": "",
        "signup_url": "https://docs.google.com/",
        "key_url": None,
        "connector": "google-docs",
        "steps": [],
    },
    {
        "slug": "google-drive",
        "name": "Google Drive",
        "category": "Google",
        "env_var": None,
        "key_prefix": "",
        "signup_url": "https://drive.google.com/",
        "key_url": None,
        "connector": "google-drive",
        "steps": [],
    },
    # ---- Cloud ----
    {
        "slug": "aws",
        "name": "Amazon Web Services",
        "category": "Cloud",
        "env_var": "AWS_ACCESS_KEY_ID",
        "key_prefix": "AKIA",
        "signup_url": "https://portal.aws.amazon.com/billing/signup",
        "key_url": "https://console.aws.amazon.com/iam/home#/security_credentials",
        "connector": None,
        "steps": [
            "Create an AWS account",
            "Open IAM -> Security credentials",
            "Create an access key",
            "Copy the Access Key ID (starts with AKIA) and paste it below",
        ],
    },
    {
        "slug": "google-cloud",
        "name": "Google Cloud Platform",
        "category": "Cloud",
        "env_var": "GOOGLE_CLOUD_API_KEY",
        "key_prefix": "AIza",
        "signup_url": "https://console.cloud.google.com/freetrial",
        "key_url": "https://console.cloud.google.com/apis/credentials",
        "connector": None,
        "steps": [
            "Create a project in the Google Cloud Console",
            "Open APIs & Services -> Credentials",
            "Click 'Create credentials' -> 'API key'",
            "Copy the key and paste it below",
        ],
    },
    {
        "slug": "azure",
        "name": "Microsoft Azure",
        "category": "Cloud",
        "env_var": "AZURE_API_KEY",
        "key_prefix": "",
        "signup_url": "https://azure.microsoft.com/free/",
        "key_url": "https://portal.azure.com/",
        "connector": None,
        "steps": [
            "Create an Azure account at azure.microsoft.com",
            "Open your resource (e.g. Azure OpenAI / Cognitive Services) in the portal",
            "Go to 'Keys and Endpoint'",
            "Copy a key and paste it below",
        ],
    },
    # ---- Productivity (Microsoft 365) ----
    {
        "slug": "microsoft-graph",
        "name": "Microsoft 365 (Graph)",
        "category": "Productivity",
        "env_var": "MICROSOFT_GRAPH_TOKEN",
        "key_prefix": "",
        "signup_url": "https://www.microsoft.com/microsoft-365",
        "key_url": "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "connector": None,
        "steps": [
            "Open the Azure Portal -> App registrations",
            "Register an app and add Microsoft Graph permissions",
            "Create a client secret under 'Certificates & secrets'",
            "Copy the secret value and paste it below",
        ],
    },
    # ---- Finance / Accounting ----
    {
        "slug": "quickbooks",
        "name": "QuickBooks Online",
        "category": "Finance",
        "env_var": "QUICKBOOKS_ACCESS_TOKEN",
        "key_prefix": "",
        "signup_url": "https://quickbooks.intuit.com/",
        "key_url": "https://developer.intuit.com/app/developer/dashboard",
        "connector": None,
        "steps": [
            "Sign in to the Intuit Developer dashboard",
            "Create an app under the QuickBooks Online API",
            "Generate OAuth credentials / access token",
            "Paste the access token below",
        ],
    },
    {
        "slug": "xero",
        "name": "Xero",
        "category": "Finance",
        "env_var": "XERO_ACCESS_TOKEN",
        "key_prefix": "",
        "signup_url": "https://www.xero.com/signup/",
        "key_url": "https://developer.xero.com/app/manage",
        "connector": None,
        "steps": [
            "Sign in to developer.xero.com",
            "Create an app and configure OAuth 2.0",
            "Generate an access token",
            "Paste it below",
        ],
    },
    # ---- CRM ----
    {
        "slug": "hubspot",
        "name": "HubSpot",
        "category": "CRM",
        "env_var": "HUBSPOT_ACCESS_TOKEN",
        "key_prefix": "pat-",
        "signup_url": "https://www.hubspot.com/",
        "key_url": "https://app.hubspot.com/private-apps",
        "connector": None,
        "steps": [
            "Sign in to HubSpot",
            "Open Settings -> Integrations -> Private Apps",
            "Create a private app and copy its access token",
            "Paste it (starts with pat-) below",
        ],
    },
    {
        "slug": "salesforce",
        "name": "Salesforce",
        "category": "CRM",
        "env_var": "SALESFORCE_ACCESS_TOKEN",
        "key_prefix": "",
        "signup_url": "https://www.salesforce.com/form/signup/freetrial-sales/",
        "key_url": "https://help.salesforce.com/s/articleView?id=sf.connected_app_create.htm",
        "connector": None,
        "steps": [
            "Sign in to Salesforce -> Setup",
            "Create a Connected App with OAuth enabled",
            "Generate / copy an access token",
            "Paste it below",
        ],
    },
    # ---- Automation (browser + bridge capabilities) ----
    {
        "slug": "browserbase",
        "name": "Browserbase (API key)",
        "category": "Automation",
        "env_var": "BROWSERBASE_API_KEY",
        "key_prefix": "bb_",
        "signup_url": "https://www.browserbase.com/",
        "key_url": "https://www.browserbase.com/settings",
        "connector": None,
        "steps": [
            "Sign in to browserbase.com",
            "Open Settings",
            "Copy your API key",
            "Paste it below, then add your Project ID as a second entry",
        ],
    },
    {
        "slug": "browserbase-project",
        "name": "Browserbase (Project ID)",
        "category": "Automation",
        "env_var": "BROWSERBASE_PROJECT_ID",
        "key_prefix": "",
        "signup_url": "https://www.browserbase.com/",
        "key_url": "https://www.browserbase.com/settings",
        "connector": None,
        "steps": [
            "Open Settings on browserbase.com",
            "Copy your Project ID",
            "Paste it below",
        ],
    },
    {
        "slug": "apify",
        "name": "Apify",
        "category": "Automation",
        "env_var": "APIFY_API_TOKEN",
        "key_prefix": "apify_api_",
        "signup_url": "https://console.apify.com/sign-up",
        "key_url": "https://console.apify.com/account/integrations",
        "connector": None,
        "steps": [
            "Sign in to console.apify.com",
            "Open Settings -> Integrations",
            "Copy your Personal API token",
            "Paste it below",
        ],
    },
    {
        "slug": "ssh-host",
        "name": "SSH Bridge (Host)",
        "category": "Automation",
        "env_var": "SSH_HOST",
        "key_prefix": "",
        "signup_url": None,
        "key_url": "https://support.apple.com/guide/mac-help/allow-a-remote-computer-to-access-your-mac-mchlp1066/mac",
        "connector": None,
        "steps": [
            "On your Mac, enable Remote Login (System Settings -> General -> Sharing)",
            "Find your Mac's reachable hostname or IP",
            "Paste it below (the Capabilities page has a guided form too)",
        ],
    },
    {
        "slug": "ssh-user",
        "name": "SSH Bridge (User)",
        "category": "Automation",
        "env_var": "SSH_USER",
        "key_prefix": "",
        "signup_url": None,
        "key_url": None,
        "connector": None,
        "steps": ["Your macOS account short username", "Paste it below"],
    },
    {
        "slug": "ssh-private-key",
        "name": "SSH Bridge (Private key)",
        "category": "Automation",
        "env_var": "SSH_PRIVATE_KEY",
        "key_prefix": "-----BEGIN",
        "signup_url": None,
        "key_url": None,
        "connector": None,
        "steps": [
            "Generate a key pair (ssh-keygen -t ed25519)",
            "Add the public key to ~/.ssh/authorized_keys on your Mac",
            "Paste the PRIVATE key contents below",
        ],
    },
    {
        "slug": "ssh-password",
        "name": "SSH Bridge (Password)",
        "category": "Automation",
        "env_var": "SSH_PASSWORD",
        "key_prefix": "",
        "signup_url": None,
        "key_url": None,
        "connector": None,
        "steps": [
            "Use only if you are not using a private key",
            "Paste your macOS login password below",
        ],
    },
]

# Keep LLM provider secret names in sync with env_1 (config/providers.toml) so
# the vault/catalog never duplicates a hardcoded env var for a managed provider.
try:
    from src.providers.config import provider_env_map as _provider_env_map
    _ENV_OVERRIDES = _provider_env_map()
    for _svc in SERVICE_CATALOG:
        _ev = _ENV_OVERRIDES.get(_svc["slug"])
        if _ev:
            _svc["env_var"] = _ev
except Exception:
    pass

_BY_SLUG = {s["slug"]: s for s in SERVICE_CATALOG}


def get_service(slug):
    return _BY_SLUG.get((slug or "").lower())


def manual_services():
    """Services that support manual API key entry (guided signup helper)."""
    return [s for s in SERVICE_CATALOG if s.get("key_url")]


def connector_services():
    """Services backed by a Replit integration / OAuth connector."""
    return [s for s in SERVICE_CATALOG if s.get("connector")]


def env_var_for(slug):
    svc = get_service(slug)
    return svc["env_var"] if svc else None


def category_for(slug):
    svc = get_service(slug)
    return svc["category"] if svc else "Other"
