"""Curated catalog of known subscription / recurring-billing services.

Discovery matches hostnames seen in the user's browser history against this
catalog to infer likely subscriptions. The catalog is deliberately
*best-effort*: a match means "the user visited this service", not "the user
definitely pays for it" — confirmation is always left to the user.

Each entry carries the URLs the login/manage flow needs:
- ``login_url``: where the sign-in form lives.
- ``account_url``: the account / billing / membership page to reach after login.
- ``cancel_hint``: human guidance for where the cancel flow tends to live.
"""

import re

SUBSCRIPTION_CATALOG = [
    {
        "slug": "netflix",
        "name": "Netflix",
        "domains": ["netflix.com"],
        "login_url": "https://www.netflix.com/login",
        "account_url": "https://www.netflix.com/account",
        "est_cost": "$15.49/mo",
        "cancel_hint": "Account → Membership & Billing → Cancel Membership",
    },
    {
        "slug": "spotify",
        "name": "Spotify",
        "domains": ["spotify.com"],
        "login_url": "https://accounts.spotify.com/en/login",
        "account_url": "https://www.spotify.com/account/subscription/",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Your plan → Change or cancel",
    },
    {
        "slug": "youtube-premium",
        "name": "YouTube Premium",
        "domains": ["youtube.com"],
        "login_url": "https://accounts.google.com/ServiceLogin",
        "account_url": "https://www.youtube.com/paid_memberships",
        "est_cost": "$13.99/mo",
        "cancel_hint": "Paid memberships → Manage membership → Deactivate",
    },
    {
        "slug": "amazon-prime",
        "name": "Amazon Prime",
        "domains": ["amazon.com", "primevideo.com"],
        "login_url": "https://www.amazon.com/ap/signin",
        "account_url": "https://www.amazon.com/gp/primecentral",
        "est_cost": "$14.99/mo",
        "cancel_hint": "Prime membership → Manage → End membership",
    },
    {
        "slug": "disney-plus",
        "name": "Disney+",
        "domains": ["disneyplus.com"],
        "login_url": "https://www.disneyplus.com/login",
        "account_url": "https://www.disneyplus.com/account/subscription",
        "est_cost": "$13.99/mo",
        "cancel_hint": "Account → Subscription → Cancel Subscription",
    },
    {
        "slug": "hulu",
        "name": "Hulu",
        "domains": ["hulu.com"],
        "login_url": "https://auth.hulu.com/web/login",
        "account_url": "https://secure.hulu.com/account",
        "est_cost": "$17.99/mo",
        "cancel_hint": "Account → Cancel Your Subscription",
    },
    {
        "slug": "hbo-max",
        "name": "Max (HBO)",
        "domains": ["max.com", "hbomax.com"],
        "login_url": "https://auth.max.com/login",
        "account_url": "https://www.max.com/account",
        "est_cost": "$16.99/mo",
        "cancel_hint": "Account → Subscription → Manage Subscription",
    },
    {
        "slug": "apple",
        "name": "Apple (iCloud / TV+ / Music)",
        "domains": ["apple.com", "icloud.com"],
        "login_url": "https://account.apple.com/sign-in",
        "account_url": "https://account.apple.com/account/manage/subscriptions",
        "est_cost": None,
        "cancel_hint": "Subscriptions → select service → Cancel Subscription",
    },
    {
        "slug": "adobe",
        "name": "Adobe Creative Cloud",
        "domains": ["adobe.com"],
        "login_url": "https://account.adobe.com/",
        "account_url": "https://account.adobe.com/plans",
        "est_cost": "$59.99/mo",
        "cancel_hint": "Plans → Manage plan → Cancel plan",
    },
    {
        "slug": "microsoft365",
        "name": "Microsoft 365",
        "domains": ["microsoft.com", "office.com"],
        "login_url": "https://login.live.com/",
        "account_url": "https://account.microsoft.com/services",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Services & subscriptions → Manage → Cancel",
    },
    {
        "slug": "google-one",
        "name": "Google One / Workspace",
        "domains": ["one.google.com"],
        "login_url": "https://accounts.google.com/ServiceLogin",
        "account_url": "https://one.google.com/settings",
        "est_cost": "$1.99/mo",
        "cancel_hint": "Settings → Cancel membership",
    },
    {
        "slug": "dropbox",
        "name": "Dropbox",
        "domains": ["dropbox.com"],
        "login_url": "https://www.dropbox.com/login",
        "account_url": "https://www.dropbox.com/account/plan",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Plan → Cancel plan",
    },
    {
        "slug": "notion",
        "name": "Notion",
        "domains": ["notion.so"],
        "login_url": "https://www.notion.so/login",
        "account_url": "https://www.notion.so/my-account",
        "est_cost": "$10/mo",
        "cancel_hint": "Settings → Plans → Change / cancel plan",
    },
    {
        "slug": "github",
        "name": "GitHub",
        "domains": ["github.com"],
        "login_url": "https://github.com/login",
        "account_url": "https://github.com/settings/billing",
        "est_cost": "$4/mo",
        "cancel_hint": "Settings → Billing and plans → downgrade to Free",
    },
    {
        "slug": "linkedin-premium",
        "name": "LinkedIn Premium",
        "domains": ["linkedin.com"],
        "login_url": "https://www.linkedin.com/login",
        "account_url": "https://www.linkedin.com/premium/manage/",
        "est_cost": "$39.99/mo",
        "cancel_hint": "Premium → Manage Premium account → Cancel subscription",
    },
    {
        "slug": "nytimes",
        "name": "The New York Times",
        "domains": ["nytimes.com"],
        "login_url": "https://myaccount.nytimes.com/auth/login",
        "account_url": "https://www.nytimes.com/subscription",
        "est_cost": "$17/mo",
        "cancel_hint": "Account → Manage Subscription → Cancel",
    },
    {
        "slug": "audible",
        "name": "Audible",
        "domains": ["audible.com"],
        "login_url": "https://www.audible.com/sign-in",
        "account_url": "https://www.audible.com/account/membership-details",
        "est_cost": "$14.95/mo",
        "cancel_hint": "Account Details → Cancel membership",
    },
    {
        "slug": "paramount-plus",
        "name": "Paramount+",
        "domains": ["paramountplus.com"],
        "login_url": "https://www.paramountplus.com/account/signin/",
        "account_url": "https://www.paramountplus.com/account/",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Cancel Subscription",
    },
    {
        "slug": "peacock",
        "name": "Peacock",
        "domains": ["peacocktv.com"],
        "login_url": "https://www.peacocktv.com/signin",
        "account_url": "https://www.peacocktv.com/account/plans",
        "est_cost": "$7.99/mo",
        "cancel_hint": "Account → Plans & Payment → Cancel plan",
    },
    {
        "slug": "patreon",
        "name": "Patreon",
        "domains": ["patreon.com"],
        "login_url": "https://www.patreon.com/login",
        "account_url": "https://www.patreon.com/settings/memberships",
        "est_cost": None,
        "cancel_hint": "Settings → Memberships → Edit → Cancel",
    },
    # -- streaming / video ---------------------------------------------------
    {
        "slug": "espn-plus",
        "name": "ESPN+",
        "domains": ["espn.com", "plus.espn.com"],
        "login_url": "https://plus.espn.com/",
        "account_url": "https://www.espn.com/watch/account",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Subscriptions → ESPN+ → Cancel Subscription",
    },
    {
        "slug": "crunchyroll",
        "name": "Crunchyroll",
        "domains": ["crunchyroll.com"],
        "login_url": "https://www.crunchyroll.com/login",
        "account_url": "https://www.crunchyroll.com/account/membership",
        "est_cost": "$7.99/mo",
        "cancel_hint": "Account → Membership → Cancel Membership",
    },
    {
        "slug": "starz",
        "name": "Starz",
        "domains": ["starz.com"],
        "login_url": "https://www.starz.com/login",
        "account_url": "https://www.starz.com/us/en/manage-account",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Manage Account → Subscription → Cancel",
    },
    {
        "slug": "discovery-plus",
        "name": "Discovery+",
        "domains": ["discoveryplus.com"],
        "login_url": "https://www.discoveryplus.com/signin",
        "account_url": "https://www.discoveryplus.com/account",
        "est_cost": "$8.99/mo",
        "cancel_hint": "Account → Manage Subscription → Cancel",
    },
    {
        "slug": "sling",
        "name": "Sling TV",
        "domains": ["sling.com"],
        "login_url": "https://www.sling.com/login",
        "account_url": "https://www.sling.com/account/subscription",
        "est_cost": "$40/mo",
        "cancel_hint": "Account → My Subscription → Cancel Subscription",
    },
    {
        "slug": "fubo",
        "name": "FuboTV",
        "domains": ["fubo.tv", "fubotv.com"],
        "login_url": "https://www.fubo.tv/signin",
        "account_url": "https://www.fubo.tv/account",
        "est_cost": "$79.99/mo",
        "cancel_hint": "Account → Subscription → Cancel",
    },
    {
        "slug": "dazn",
        "name": "DAZN",
        "domains": ["dazn.com"],
        "login_url": "https://www.dazn.com/signin",
        "account_url": "https://www.dazn.com/account",
        "est_cost": "$24.99/mo",
        "cancel_hint": "My Account → Plans → Cancel subscription",
    },
    # -- music / audio -------------------------------------------------------
    {
        "slug": "tidal",
        "name": "Tidal",
        "domains": ["tidal.com"],
        "login_url": "https://login.tidal.com/",
        "account_url": "https://account.tidal.com/",
        "est_cost": "$10.99/mo",
        "cancel_hint": "Account → Manage subscription → Cancel",
    },
    {
        "slug": "siriusxm",
        "name": "SiriusXM",
        "domains": ["siriusxm.com"],
        "login_url": "https://www.siriusxm.com/login",
        "account_url": "https://www.siriusxm.com/account",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Manage Subscription → Cancel",
    },
    {
        "slug": "pandora",
        "name": "Pandora",
        "domains": ["pandora.com"],
        "login_url": "https://www.pandora.com/account/sign-in",
        "account_url": "https://www.pandora.com/account/subscription",
        "est_cost": "$10.99/mo",
        "cancel_hint": "Settings → Subscription → Cancel",
    },
    {
        "slug": "deezer",
        "name": "Deezer",
        "domains": ["deezer.com"],
        "login_url": "https://www.deezer.com/login",
        "account_url": "https://www.deezer.com/account",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → My subscription → Cancel",
    },
    # -- vpn / security / passwords -----------------------------------------
    {
        "slug": "nordvpn",
        "name": "NordVPN",
        "domains": ["nordvpn.com"],
        "login_url": "https://my.nordaccount.com/login/",
        "account_url": "https://my.nordaccount.com/billing/subscriptions/",
        "est_cost": "$12.99/mo",
        "cancel_hint": "Nord Account → Billing → Subscriptions → Cancel auto-renewal",
    },
    {
        "slug": "expressvpn",
        "name": "ExpressVPN",
        "domains": ["expressvpn.com"],
        "login_url": "https://www.expressvpn.com/sign-in",
        "account_url": "https://www.expressvpn.com/subscriptions",
        "est_cost": "$12.95/mo",
        "cancel_hint": "My Account → Manage Subscription → Turn off auto-renew",
    },
    {
        "slug": "surfshark",
        "name": "Surfshark",
        "domains": ["surfshark.com"],
        "login_url": "https://my.surfshark.com/login",
        "account_url": "https://my.surfshark.com/account/subscriptions",
        "est_cost": "$12.95/mo",
        "cancel_hint": "Account → Subscription → Cancel subscription",
    },
    {
        "slug": "proton",
        "name": "Proton",
        "domains": ["proton.me", "protonmail.com"],
        "login_url": "https://account.proton.me/login",
        "account_url": "https://account.proton.me/u/0/dashboard",
        "est_cost": "$4.99/mo",
        "cancel_hint": "Dashboard → Subscription → Downgrade / cancel plan",
    },
    {
        "slug": "1password",
        "name": "1Password",
        "domains": ["1password.com"],
        "login_url": "https://my.1password.com/signin",
        "account_url": "https://my.1password.com/billing",
        "est_cost": "$2.99/mo",
        "cancel_hint": "Account → Billing → Cancel subscription",
    },
    {
        "slug": "lastpass",
        "name": "LastPass",
        "domains": ["lastpass.com"],
        "login_url": "https://lastpass.com/login.php",
        "account_url": "https://lastpass.com/?ac=1",
        "est_cost": "$3/mo",
        "cancel_hint": "Account Settings → My Subscription → Cancel",
    },
    {
        "slug": "dashlane",
        "name": "Dashlane",
        "domains": ["dashlane.com"],
        "login_url": "https://app.dashlane.com/",
        "account_url": "https://app.dashlane.com/#/settings/account",
        "est_cost": "$4.99/mo",
        "cancel_hint": "Settings → Subscription → Cancel subscription",
    },
    # -- productivity / software --------------------------------------------
    {
        "slug": "slack",
        "name": "Slack",
        "domains": ["slack.com"],
        "login_url": "https://slack.com/signin",
        "account_url": "https://my.slack.com/admin/billing",
        "est_cost": "$8.75/mo",
        "cancel_hint": "Workspace admin → Billing → Cancel / downgrade plan",
    },
    {
        "slug": "zoom",
        "name": "Zoom",
        "domains": ["zoom.us", "zoom.com"],
        "login_url": "https://zoom.us/signin",
        "account_url": "https://zoom.us/billing",
        "est_cost": "$13.33/mo",
        "cancel_hint": "Account Management → Billing → Cancel Subscription",
    },
    {
        "slug": "canva",
        "name": "Canva",
        "domains": ["canva.com"],
        "login_url": "https://www.canva.com/login/",
        "account_url": "https://www.canva.com/settings/billing-and-teams",
        "est_cost": "$14.99/mo",
        "cancel_hint": "Account settings → Billing & plans → Cancel subscription",
    },
    {
        "slug": "grammarly",
        "name": "Grammarly",
        "domains": ["grammarly.com"],
        "login_url": "https://account.grammarly.com/signin",
        "account_url": "https://account.grammarly.com/subscription",
        "est_cost": "$12/mo",
        "cancel_hint": "Account → Subscription → Cancel Subscription",
    },
    {
        "slug": "evernote",
        "name": "Evernote",
        "domains": ["evernote.com"],
        "login_url": "https://www.evernote.com/Login.action",
        "account_url": "https://www.evernote.com/AccountSummary.action",
        "est_cost": "$14.99/mo",
        "cancel_hint": "Settings → Account summary → Manage subscription → Cancel",
    },
    {
        "slug": "todoist",
        "name": "Todoist",
        "domains": ["todoist.com"],
        "login_url": "https://todoist.com/auth/login",
        "account_url": "https://todoist.com/app/settings/subscription",
        "est_cost": "$5/mo",
        "cancel_hint": "Settings → Subscription → Cancel plan",
    },
    {
        "slug": "asana",
        "name": "Asana",
        "domains": ["asana.com"],
        "login_url": "https://app.asana.com/-/login",
        "account_url": "https://app.asana.com/0/admin/billing",
        "est_cost": "$10.99/mo",
        "cancel_hint": "Admin console → Billing → Cancel plan",
    },
    {
        "slug": "trello",
        "name": "Trello",
        "domains": ["trello.com"],
        "login_url": "https://trello.com/login",
        "account_url": "https://trello.com/your/billing",
        "est_cost": "$5/mo",
        "cancel_hint": "Workspace → Billing → Cancel subscription",
    },
    {
        "slug": "atlassian",
        "name": "Atlassian (Jira / Confluence)",
        "domains": ["atlassian.com", "atlassian.net"],
        "login_url": "https://id.atlassian.com/login",
        "account_url": "https://admin.atlassian.com/billing",
        "est_cost": None,
        "cancel_hint": "Admin → Billing → Manage subscriptions → Cancel",
    },
    {
        "slug": "figma",
        "name": "Figma",
        "domains": ["figma.com"],
        "login_url": "https://www.figma.com/login",
        "account_url": "https://www.figma.com/files/account",
        "est_cost": "$12/mo",
        "cancel_hint": "Admin → Billing → Manage → Cancel plan",
    },
    {
        "slug": "squarespace",
        "name": "Squarespace",
        "domains": ["squarespace.com"],
        "login_url": "https://login.squarespace.com/",
        "account_url": "https://account.squarespace.com/subscriptions",
        "est_cost": "$16/mo",
        "cancel_hint": "Account → Subscriptions → Cancel subscription",
    },
    {
        "slug": "wix",
        "name": "Wix",
        "domains": ["wix.com"],
        "login_url": "https://users.wix.com/signin",
        "account_url": "https://www.wix.com/account/subscriptions",
        "est_cost": "$17/mo",
        "cancel_hint": "Account → Subscriptions → Cancel plan",
    },
    {
        "slug": "shopify",
        "name": "Shopify",
        "domains": ["shopify.com"],
        "login_url": "https://accounts.shopify.com/store-login",
        "account_url": "https://admin.shopify.com/settings/billing",
        "est_cost": "$39/mo",
        "cancel_hint": "Settings → Plan → Deactivate / cancel subscription",
    },
    {
        "slug": "wordpress",
        "name": "WordPress.com",
        "domains": ["wordpress.com"],
        "login_url": "https://wordpress.com/log-in",
        "account_url": "https://wordpress.com/me/purchases",
        "est_cost": "$4/mo",
        "cancel_hint": "Me → Purchases → select plan → Cancel subscription",
    },
    # -- cloud / dev hosting / storage --------------------------------------
    {
        "slug": "digitalocean",
        "name": "DigitalOcean",
        "domains": ["digitalocean.com"],
        "login_url": "https://cloud.digitalocean.com/login",
        "account_url": "https://cloud.digitalocean.com/account/billing",
        "est_cost": None,
        "cancel_hint": "Account → Billing → Close / downgrade resources",
    },
    {
        "slug": "vercel",
        "name": "Vercel",
        "domains": ["vercel.com"],
        "login_url": "https://vercel.com/login",
        "account_url": "https://vercel.com/account/billing",
        "est_cost": "$20/mo",
        "cancel_hint": "Settings → Billing → Cancel / downgrade plan",
    },
    {
        "slug": "netlify",
        "name": "Netlify",
        "domains": ["netlify.com"],
        "login_url": "https://app.netlify.com/login",
        "account_url": "https://app.netlify.com/teams/billing",
        "est_cost": "$19/mo",
        "cancel_hint": "Team settings → Billing → Cancel plan",
    },
    {
        "slug": "gitlab",
        "name": "GitLab",
        "domains": ["gitlab.com"],
        "login_url": "https://gitlab.com/users/sign_in",
        "account_url": "https://gitlab.com/-/profile/billings",
        "est_cost": "$29/mo",
        "cancel_hint": "Billing → Manage subscription → Cancel",
    },
    {
        "slug": "jetbrains",
        "name": "JetBrains",
        "domains": ["jetbrains.com"],
        "login_url": "https://account.jetbrains.com/login",
        "account_url": "https://account.jetbrains.com/licenses",
        "est_cost": None,
        "cancel_hint": "Account → Licenses → Manage subscription → Cancel",
    },
    {
        "slug": "backblaze",
        "name": "Backblaze",
        "domains": ["backblaze.com"],
        "login_url": "https://secure.backblaze.com/user_signin.htm",
        "account_url": "https://secure.backblaze.com/account_settings.htm",
        "est_cost": "$9/mo",
        "cancel_hint": "Account → My Settings → Cancel subscription",
    },
    {
        "slug": "box",
        "name": "Box",
        "domains": ["box.com"],
        "login_url": "https://account.box.com/login",
        "account_url": "https://app.box.com/account/billing",
        "est_cost": "$14/mo",
        "cancel_hint": "Account Settings → Billing → Cancel plan",
    },
    # -- news / reading ------------------------------------------------------
    {
        "slug": "wsj",
        "name": "The Wall Street Journal",
        "domains": ["wsj.com"],
        "login_url": "https://accounts.wsj.com/login",
        "account_url": "https://customercenter.wsj.com/",
        "est_cost": "$38.99/mo",
        "cancel_hint": "Customer Center → Manage subscription → Cancel",
    },
    {
        "slug": "washington-post",
        "name": "The Washington Post",
        "domains": ["washingtonpost.com"],
        "login_url": "https://www.washingtonpost.com/subscribe/signin/",
        "account_url": "https://www.washingtonpost.com/my-post/",
        "est_cost": "$12/mo",
        "cancel_hint": "My Post → Manage subscription → Cancel subscription",
    },
    {
        "slug": "economist",
        "name": "The Economist",
        "domains": ["economist.com"],
        "login_url": "https://www.economist.com/api/auth/login",
        "account_url": "https://myaccount.economist.com/",
        "est_cost": "$24.90/mo",
        "cancel_hint": "My Account → Subscriptions → Cancel auto-renewal",
    },
    {
        "slug": "bloomberg",
        "name": "Bloomberg",
        "domains": ["bloomberg.com"],
        "login_url": "https://www.bloomberg.com/account/signin",
        "account_url": "https://www.bloomberg.com/account/",
        "est_cost": "$34.99/mo",
        "cancel_hint": "Account → Manage subscription → Cancel",
    },
    {
        "slug": "substack",
        "name": "Substack",
        "domains": ["substack.com"],
        "login_url": "https://substack.com/sign-in",
        "account_url": "https://substack.com/settings",
        "est_cost": None,
        "cancel_hint": "Settings → Subscriptions → Manage → Cancel",
    },
    {
        "slug": "medium",
        "name": "Medium",
        "domains": ["medium.com"],
        "login_url": "https://medium.com/m/signin",
        "account_url": "https://medium.com/me/settings/membership",
        "est_cost": "$5/mo",
        "cancel_hint": "Settings → Membership → Cancel membership",
    },
    {
        "slug": "athletic",
        "name": "The Athletic",
        "domains": ["theathletic.com"],
        "login_url": "https://theathletic.com/login/",
        "account_url": "https://theathletic.com/account/",
        "est_cost": "$7.99/mo",
        "cancel_hint": "Account → Manage subscription → Cancel",
    },
    {
        "slug": "scribd",
        "name": "Scribd / Everand",
        "domains": ["scribd.com", "everand.com"],
        "login_url": "https://www.scribd.com/login",
        "account_url": "https://www.scribd.com/account-settings/",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account settings → Membership → Cancel membership",
    },
    # -- learning ------------------------------------------------------------
    {
        "slug": "coursera",
        "name": "Coursera",
        "domains": ["coursera.org"],
        "login_url": "https://www.coursera.org/login",
        "account_url": "https://www.coursera.org/account-profile",
        "est_cost": "$59/mo",
        "cancel_hint": "Settings → Manage subscription → Cancel",
    },
    {
        "slug": "udemy",
        "name": "Udemy",
        "domains": ["udemy.com"],
        "login_url": "https://www.udemy.com/join/login-popup/",
        "account_url": "https://www.udemy.com/user/edit-account/",
        "est_cost": None,
        "cancel_hint": "Subscriptions → Manage → Cancel subscription",
    },
    {
        "slug": "skillshare",
        "name": "Skillshare",
        "domains": ["skillshare.com"],
        "login_url": "https://www.skillshare.com/login",
        "account_url": "https://www.skillshare.com/settings/payments",
        "est_cost": "$13.99/mo",
        "cancel_hint": "Settings → Membership → Cancel membership",
    },
    {
        "slug": "duolingo",
        "name": "Duolingo",
        "domains": ["duolingo.com"],
        "login_url": "https://www.duolingo.com/?isLoggingIn=true",
        "account_url": "https://www.duolingo.com/settings/account",
        "est_cost": "$6.99/mo",
        "cancel_hint": "Settings → Subscription → Cancel Super / Max",
    },
    {
        "slug": "masterclass",
        "name": "MasterClass",
        "domains": ["masterclass.com"],
        "login_url": "https://www.masterclass.com/auth/login",
        "account_url": "https://www.masterclass.com/account",
        "est_cost": "$10/mo",
        "cancel_hint": "Account → Manage subscription → Cancel",
    },
    {
        "slug": "chegg",
        "name": "Chegg",
        "domains": ["chegg.com"],
        "login_url": "https://www.chegg.com/auth",
        "account_url": "https://www.chegg.com/my/orders",
        "est_cost": "$15.95/mo",
        "cancel_hint": "My account → Orders/Subscriptions → Cancel",
    },
    {
        "slug": "babbel",
        "name": "Babbel",
        "domains": ["babbel.com"],
        "login_url": "https://my.babbel.com/en/accounts/sign_in",
        "account_url": "https://my.babbel.com/en/account/subscriptions",
        "est_cost": "$13.95/mo",
        "cancel_hint": "Account → Subscriptions → Cancel renewal",
    },
    # -- fitness / health ----------------------------------------------------
    {
        "slug": "peloton",
        "name": "Peloton",
        "domains": ["onepeloton.com", "peloton.com"],
        "login_url": "https://members.onepeloton.com/login",
        "account_url": "https://members.onepeloton.com/preferences/membership",
        "est_cost": "$44/mo",
        "cancel_hint": "Membership → Manage → Cancel membership",
    },
    {
        "slug": "strava",
        "name": "Strava",
        "domains": ["strava.com"],
        "login_url": "https://www.strava.com/login",
        "account_url": "https://www.strava.com/settings/subscription",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Settings → Subscription → Cancel subscription",
    },
    {
        "slug": "headspace",
        "name": "Headspace",
        "domains": ["headspace.com"],
        "login_url": "https://www.headspace.com/login",
        "account_url": "https://www.headspace.com/subscription",
        "est_cost": "$12.99/mo",
        "cancel_hint": "Account → Subscription → Cancel subscription",
    },
    {
        "slug": "calm",
        "name": "Calm",
        "domains": ["calm.com"],
        "login_url": "https://www.calm.com/login",
        "account_url": "https://www.calm.com/profile",
        "est_cost": "$14.99/mo",
        "cancel_hint": "Profile → Manage subscription → Cancel",
    },
    {
        "slug": "myfitnesspal",
        "name": "MyFitnessPal",
        "domains": ["myfitnesspal.com"],
        "login_url": "https://www.myfitnesspal.com/account/login",
        "account_url": "https://www.myfitnesspal.com/account/manage_subscription",
        "est_cost": "$19.99/mo",
        "cancel_hint": "Account → Manage subscription → Cancel premium",
    },
    {
        "slug": "whoop",
        "name": "WHOOP",
        "domains": ["whoop.com"],
        "login_url": "https://app.whoop.com/login",
        "account_url": "https://app.whoop.com/account",
        "est_cost": "$30/mo",
        "cancel_hint": "Membership → Manage → Cancel membership",
    },
    # -- gaming --------------------------------------------------------------
    {
        "slug": "playstation-plus",
        "name": "PlayStation Plus",
        "domains": ["playstation.com"],
        "login_url": "https://www.playstation.com/sign-in/",
        "account_url": "https://www.playstation.com/account/subscriptions/",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Subscriptions → Turn off auto-renew",
    },
    {
        "slug": "xbox",
        "name": "Xbox Game Pass",
        "domains": ["xbox.com"],
        "login_url": "https://www.xbox.com/login",
        "account_url": "https://account.microsoft.com/services",
        "est_cost": "$16.99/mo",
        "cancel_hint": "Microsoft account → Services & subscriptions → Cancel",
    },
    {
        "slug": "nintendo",
        "name": "Nintendo Switch Online",
        "domains": ["nintendo.com"],
        "login_url": "https://accounts.nintendo.com/login",
        "account_url": "https://accounts.nintendo.com/subscription",
        "est_cost": "$3.99/mo",
        "cancel_hint": "Account → Shop menu → Subscriptions → Turn off auto-renewal",
    },
    {
        "slug": "twitch",
        "name": "Twitch",
        "domains": ["twitch.tv"],
        "login_url": "https://www.twitch.tv/login",
        "account_url": "https://www.twitch.tv/subscriptions",
        "est_cost": "$4.99/mo",
        "cancel_hint": "Subscriptions → select channel → Don't renew / Cancel",
    },
    {
        "slug": "discord",
        "name": "Discord Nitro",
        "domains": ["discord.com"],
        "login_url": "https://discord.com/login",
        "account_url": "https://discord.com/settings/subscriptions",
        "est_cost": "$9.99/mo",
        "cancel_hint": "User Settings → Subscriptions → Cancel Nitro",
    },
    {
        "slug": "roblox",
        "name": "Roblox Premium",
        "domains": ["roblox.com"],
        "login_url": "https://www.roblox.com/login",
        "account_url": "https://www.roblox.com/premium/membership",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Premium → Membership → Cancel Renewal",
    },
    # -- food / delivery memberships ----------------------------------------
    {
        "slug": "doordash",
        "name": "DoorDash (DashPass)",
        "domains": ["doordash.com"],
        "login_url": "https://www.doordash.com/consumer/login/",
        "account_url": "https://www.doordash.com/dashpass/",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Manage DashPass → End Subscription",
    },
    {
        "slug": "uber-one",
        "name": "Uber One",
        "domains": ["uber.com"],
        "login_url": "https://auth.uber.com/login/",
        "account_url": "https://www.uber.com/account/uber-one",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Uber One → Manage membership → Cancel",
    },
    {
        "slug": "instacart",
        "name": "Instacart+",
        "domains": ["instacart.com"],
        "login_url": "https://www.instacart.com/login",
        "account_url": "https://www.instacart.com/store/account/subscription",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Instacart+ → Cancel membership",
    },
    {
        "slug": "hellofresh",
        "name": "HelloFresh",
        "domains": ["hellofresh.com"],
        "login_url": "https://www.hellofresh.com/login",
        "account_url": "https://www.hellofresh.com/my-account/deliveries/menu",
        "est_cost": None,
        "cancel_hint": "Account settings → Plan settings → Cancel plan",
    },
    {
        "slug": "grubhub",
        "name": "Grubhub+",
        "domains": ["grubhub.com"],
        "login_url": "https://www.grubhub.com/login",
        "account_url": "https://www.grubhub.com/account",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Account → Grubhub+ membership → Cancel",
    },
    # -- shopping / warehouse memberships -----------------------------------
    {
        "slug": "costco",
        "name": "Costco",
        "domains": ["costco.com"],
        "login_url": "https://www.costco.com/LogonForm",
        "account_url": "https://www.costco.com/AccountHandlerView",
        "est_cost": "$5/mo",
        "cancel_hint": "Membership → Cancel auto-renewal (or cancel in warehouse)",
    },
    {
        "slug": "walmart-plus",
        "name": "Walmart+",
        "domains": ["walmart.com"],
        "login_url": "https://www.walmart.com/account/login",
        "account_url": "https://www.walmart.com/plus/manage",
        "est_cost": "$12.95/mo",
        "cancel_hint": "Account → Walmart+ → Manage membership → Cancel",
    },
    # -- dating --------------------------------------------------------------
    {
        "slug": "tinder",
        "name": "Tinder",
        "domains": ["tinder.com"],
        "login_url": "https://tinder.com/app/login",
        "account_url": "https://account.gotinder.com/",
        "est_cost": None,
        "cancel_hint": "Settings → Manage Payment Account → Cancel subscription",
    },
    {
        "slug": "bumble",
        "name": "Bumble",
        "domains": ["bumble.com"],
        "login_url": "https://bumble.com/get-started",
        "account_url": "https://bumble.com/app/settings",
        "est_cost": None,
        "cancel_hint": "Settings → Subscription → Cancel (or via app store)",
    },
    {
        "slug": "hinge",
        "name": "Hinge",
        "domains": ["hinge.co"],
        "login_url": "https://hinge.co/login",
        "account_url": "https://hinge.co/account",
        "est_cost": None,
        "cancel_hint": "Settings → Subscription → Cancel (or via app store)",
    },
    {
        "slug": "match",
        "name": "Match",
        "domains": ["match.com"],
        "login_url": "https://www.match.com/login",
        "account_url": "https://www.match.com/account/subscription",
        "est_cost": None,
        "cancel_hint": "Account → Subscription status → Cancel subscription",
    },
    # -- ai tools ------------------------------------------------------------
    {
        "slug": "openai",
        "name": "ChatGPT Plus (OpenAI)",
        "domains": ["openai.com", "chatgpt.com"],
        "login_url": "https://chatgpt.com/auth/login",
        "account_url": "https://chatgpt.com/#settings/Subscription",
        "est_cost": "$20/mo",
        "cancel_hint": "Settings → Subscription → Manage → Cancel plan",
    },
    {
        "slug": "anthropic",
        "name": "Claude (Anthropic)",
        "domains": ["claude.ai", "anthropic.com"],
        "login_url": "https://claude.ai/login",
        "account_url": "https://claude.ai/settings/billing",
        "est_cost": "$20/mo",
        "cancel_hint": "Settings → Billing → Manage subscription → Cancel",
    },
    {
        "slug": "midjourney",
        "name": "Midjourney",
        "domains": ["midjourney.com"],
        "login_url": "https://www.midjourney.com/account",
        "account_url": "https://www.midjourney.com/account",
        "est_cost": "$10/mo",
        "cancel_hint": "Manage Subscription → Cancel Plan",
    },
    {
        "slug": "perplexity",
        "name": "Perplexity",
        "domains": ["perplexity.ai"],
        "login_url": "https://www.perplexity.ai/",
        "account_url": "https://www.perplexity.ai/settings/account",
        "est_cost": "$20/mo",
        "cancel_hint": "Settings → Subscription → Cancel subscription",
    },
]

_BY_DOMAIN = {}
for _entry in SUBSCRIPTION_CATALOG:
    for _d in _entry["domains"]:
        _BY_DOMAIN[_d.lower()] = _entry


def match_host(host):
    """Return the catalog entry for a hostname, or None.

    Matches the host exactly or as a subdomain of a known billing domain
    (e.g. ``accounts.spotify.com`` -> Spotify).
    """
    host = (host or "").lower().strip()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if host in _BY_DOMAIN:
        return _BY_DOMAIN[host]
    for domain, entry in _BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return entry
    return None


# Generic words inside service names that must NOT, on their own, match a
# merchant — they are too common and would cause false positives in receipts.
_NAME_STOPWORDS = {
    "premium", "plus", "one", "music", "video", "creative", "cloud",
    "workspace", "the", "and", "tv", "hbo", "icloud", "office", "365",
    # Too-common words that appear inside new merchant names — keep them from
    # matching receipts on their own (these merchants still match by domain,
    # and, where they have one, by a distinctive brand keyword).
    "play", "game", "pass", "online", "switch", "match", "medium", "calm",
    "blue", "post", "times", "wall", "street",
}


def _name_keywords(entry):
    """Distinctive lowercase keywords for an entry's name/slug (>=4 chars)."""
    words = set()
    name = entry["name"].split("(")[0]
    for token in re.split(r"[^a-z0-9]+", name.lower()):
        if len(token) >= 4 and token not in _NAME_STOPWORDS:
            words.add(token)
    for token in entry["slug"].split("-"):
        if len(token) >= 4 and token not in _NAME_STOPWORDS:
            words.add(token)
    return words


_NAME_KEYWORDS = {e["slug"]: _name_keywords(e) for e in SUBSCRIPTION_CATALOG}


def match_merchant(from_addr=None, text=None):
    """Best-effort match of a billing email to a catalog service.

    Tries the strongest signal first — the sender's domain (e.g. a receipt from
    ``billing@netflix.com`` maps to Netflix) — then falls back to a distinctive
    service-name keyword appearing as a whole word in ``text`` (subject/body).

    Returns the catalog entry or None. A None is honest: the email looks like a
    receipt but is not for a service MyDude recognises, so the user can still
    add it by hand.
    """
    # 1) Sender domain.
    addr = (from_addr or "").strip().lower()
    if "@" in addr:
        domain = addr.rsplit("@", 1)[-1].strip()
        entry = match_host(domain)
        if entry:
            return entry

    # 2) Distinctive name keyword as a whole word in the email text.
    blob = (text or "").lower()
    if blob:
        for entry in SUBSCRIPTION_CATALOG:
            for kw in _NAME_KEYWORDS.get(entry["slug"], ()):
                if re.search(r"\b%s\b" % re.escape(kw), blob):
                    return entry
    return None


def all_services():
    """All catalog entries (for manual-add suggestions in the UI)."""
    return list(SUBSCRIPTION_CATALOG)


def get_service(slug):
    for entry in SUBSCRIPTION_CATALOG:
        if entry["slug"] == (slug or "").lower():
            return entry
    return None
