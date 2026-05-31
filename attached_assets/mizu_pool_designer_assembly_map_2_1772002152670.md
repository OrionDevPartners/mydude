# MIZU AGENTIC POOL DESIGNER — ASSEMBLY MAP
## Every Capability Already Exists. We're Just Wiring It Together.

---

## THE MENTAL MODEL

This is not a software development project. This is an **assembly project**. Every single capability you described already exists as a production-ready API, service, or open-source library. The job is:

1. **Identify** what already exists (done — see below)
2. **Wire** them together through a central nervous system (our app)
3. **Deploy** all streams in parallel with your army of operators

Think of it like building a pool. You don't manufacture the rebar, the gunite, the PebbleTec, the Pentair pump, or the travertine. You source them, assemble them, and the result is greater than the sum of its parts. Same thing here.

---

## THE COMPLETE PLUG-AND-PLAY MAP

Every capability you described, mapped to what already exists and is ready to connect TODAY.

---

### PROPERTY INTELLIGENCE — "Type in an address, see the property"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Geocode an address to lat/lng | **Google Maps Geocoding API** — production, billions of calls/day globally | `POST` with address string → returns lat/lng, formatted address, place ID. One API call. |
| See the property from above (satellite) | **Google Maps Static API** — instant satellite image at any zoom | One URL returns a rendered satellite image. Literally `<img src="...">` |
| See the property in high-res aerial with measurements | **EagleView Imagery API** — 3.5B images, 1" ground resolution, oblique + ortho, 98% measurement accuracy | REST API: send address → get aerial imagery + property measurements + 3D model data. **Free developer trial launched Nov 2025.** |
| Get parcel boundaries (where can the pool go?) | **Regrid API** — nationwide parcel data, GeoJSON boundaries | Send address → get exact lot lines as drawable polygons. Overlay on aerial image instantly. |
| Get property details (lot size, home sqft, year built, value) | **ATTOM Data API** — 158 million properties | Send address → get 200+ property attributes. Pre-built. One call. |
| Get elevation/grade (does the yard slope?) | **Google Maps Elevation API** | Send lat/lng points → get elevation in meters. Calculate slope automatically. |
| Upload a survey (PDF/image) and extract data | **Claude Vision API** (Anthropic) | Send survey image → Claude reads it, extracts dimensions, setbacks, easements, utilities as structured JSON. Works NOW. |
| Upload a survey (DWG/DXF CAD file) | **ezdxf** (Python, open source, pip install) | Parse CAD file programmatically. Extract layers, dimensions, annotations. Production library. |

**Net result**: User types address → in under 5 seconds they see their property from above with lot lines, measurements, and a buildable zone overlay. No human intervention.

---

### 3D POOL DESIGN — "Configure every detail, see it in 3D"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Interactive 3D pool viewer in the browser | **Three.js** + **React Three Fiber** — the global standard for browser 3D, used by Nike, Apple, Google | npm install. Renders 60fps 3D scenes with orbit camera, zoom, pan. Free, open source, production-proven. |
| Realistic water rendering (reflections, caustics) | **Three.js Water Pro** — FFT wave simulation, Fresnel reflections, subsurface scattering, caustics | Drop-in Three.js addon. Film-quality water in the browser. Multiple presets from calm pool to rough ocean. |
| Parametric pool shape generation | **CadQuery** (Python, open source) — precision B-rep solid geometry on OpenCascade kernel | Define pool shape with parameters (length, width, depth profile, radii) → generates engineering-grade 3D geometry. Exports STEP, STL, glTF. |
| Photorealistic final renders | **Blender** (free, open source) + **bpy** Python API — used by Netflix, Marvel for actual VFX production | Script Blender headless: import scene → apply materials → set cameras → render photorealistic images via Cycles GPU renderer. Runs on AWS G5 instances. |
| Real-time material preview (change tile, see it instantly) | **Three.js PBR Materials** — physically based rendering built into Three.js core | Load PBR texture sets (albedo, normal, roughness, metallic) → apply to any mesh in real-time. Swap materials with zero reload. |
| 3D model interchange format | **glTF 2.0** — the "JPEG of 3D", universal standard | Supported by everything: Three.js, Blender, Unreal, Unity, Spline, every viewer. One format, everywhere. |
| Embeddable interactive 3D on any website | **Spline** — or self-hosted Three.js viewer as Web Component | Spline: export scene → paste `<spline-viewer>` tag on any site. Or build custom viewer as `<mizu-pool-designer>` Web Component. |

**Net result**: User configures pool → real-time 3D preview updates instantly → "Generate Renders" → photorealistic images in 2-5 minutes from GPU cloud.

---

### CAD-GRADE PERMIT PLANS — "Actual construction documents"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Generate DXF/DWG construction drawings | **ezdxf** (Python) — full DXF read/write with layers, dimensions, viewports, title blocks | Define pool geometry + plumbing + electrical → ezdxf generates multi-sheet permit-ready DXF files with proper layering, dimensions, scales. |
| Engineering-grade 3D solid models | **CadQuery** (Python) — parametric BREP modeling | Pool shell, equipment pad, plumbing runs as solid geometry → export STEP/IGES for structural analysis. |
| PDF plan sets with cover sheets | **ReportLab** or **WeasyPrint** (Python) | Generate professional PDF packages: cover sheet, table of contents, plan sheets, equipment schedules, engineering calcs. |
| DXF to DWG conversion | **LibreOffice headless** or **ODA File Converter** (free) | Batch convert DXF → DWG for jurisdictions that require native DWG format. |
| Automated plumbing calculations | **Python + numpy** | Pipe sizing, flow rates, total dynamic head, friction loss — all standard hydraulic formulas. Code exists in every engineering textbook. |
| Structural calculations (shell thickness, rebar) | **Python + engineering formulas** from ACI 318/350 | Standard gunite pool engineering: soil bearing → shell thickness → rebar schedule. Formulaic, not AI. |

**Net result**: Design finalized → system generates complete permit-ready plan set (site plan, pool plan, cross sections, plumbing, electrical, equipment schedule) as DXF + PDF. Ready to submit.

---

### COST ESTIMATION — "±10% accuracy"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Equipment pricing (Hayward, Pentair, Jandy) | **Admin-curated PostgreSQL catalog** seeded from published dealer price lists | You know the dealer costs. Seed them once, update quarterly. Admin UI to manage. |
| Material pricing (plaster, tile, pavers, rebar, plumbing) | **Admin-curated pricing tables** by region | Your LTI list + supplier quotes → database tables. Region-specific. Admin-adjustable. |
| Tile/deck material catalogs | **NPT, Daltile, MSI, Belgard catalogs** (scraped + curated) | One-time import of product images + SKUs + pricing. Admin reviews. Future: direct vendor API partnerships. |
| Quantity takeoff (how much material for this design?) | **Parametric calculation from design config** | Pool surface area → plaster quantity. Perimeter → coping + tile. Deck area → paver quantity. All derived from the 3D model geometry. Automatic. |
| Labor cost estimation | **Admin-managed labor rates by task type** | Excavation $/cuyd, gunite $/sqft, plumbing $/connection, electrical $/circuit. Your rates, your market knowledge. |
| Cost formula engine with multipliers | **PostgreSQL + JSON rules engine** | Multiplier conditions: depth > 6ft = 1.15x, difficult access = 1.25x, infinity edge = base + $/lnft. Config-driven, admin-adjustable. |

**Net result**: Every design change recalculates cost in real-time. User sees estimate range (±10%) updating live as they configure. No manual estimation required.

---

### EQUIPMENT CONFIGURATOR — "Dropdown menus of every option"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Hayward product catalog (pumps, filters, heaters, automation, sanitization, cleaners, lights) | **Hayward published catalogs + dealer portal data** | Seed database: TriStar VS 900, Super Pump VS, OmniLogic, OmniPL, OmniHub, AquaRite, TigerShark, ColorLogic. Model #, specs, MSRP, dealer cost. |
| Pentair product catalog | **Pentair 2025-2026 Pool Product Catalog** (published online) | Seed: IntelliFlo3, SuperFlo VS, IntelliCenter, EasyTouch, IntelliChlor, IntelliBrite, Kreepy Krauly. Full spec sheets available. |
| Jandy/Zodiac product catalog | **Jandy published catalogs + iAquaLink ecosystem data** | Seed: VS FloPro, AquaLink RS, iAquaLink, AquaPure, Ray-Vac, Nicheless LED. Cross-compatibility matrix with Hayward/Pentair. |
| Smart equipment recommendation based on pool size | **Rule engine** (if pool volume > X gallons → recommend Y HP pump, Z sqft filter) | Standard pool industry sizing formulas. Turnover rate × volume = flow rate → pump selection → filter selection → heater BTU. Formulaic. |
| Equipment compatibility matrix | **PostgreSQL JSONB** | Store which products work together. OmniLogic controls Hayward + Pentair VSF pumps. IntelliCenter controls only Pentair. AquaLink RS works with all three brands. |

**Net result**: User selects brand preference → system shows only compatible products → auto-recommends correct sizing → equipment schedule auto-generated with model numbers, specs, pricing.

---

### CLIENT DELIVERY — "Send proposal, sign contract, collect deposit"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Generate beautiful proposal PDFs | **ReportLab** / **WeasyPrint** / **Puppeteer** (HTML→PDF) | Template-driven: design summary, renders, cost breakdown, timeline, terms → professional PDF. Or generate HTML proposal page → Puppeteer renders to PDF. |
| Digital signature on contract | **DocuSign API** — embedded signing, client signs without leaving your app | REST API: create envelope with contract PDF → generate embedded signing URL → client signs in-app. Track status via webhooks. Free developer account. |
| Alternative: **Dropbox Sign (HelloSign) API** | Simpler API, lower cost than DocuSign | Same flow, developer-friendly, $200/mo for API access. |
| Alternative: **DocuSeal** (open source, self-hosted) | **Free**, full eSignature with API + embedding | Host it yourself on AWS. Zero per-envelope cost. Full API. React/Vue embeds. |
| Collect deposit via credit card | **Stripe Payments** — the global standard | `stripe.paymentIntents.create()` → hosted checkout or embedded payment form. PCI compliant. Instant. |
| Collect deposit via ACH bank transfer | **Stripe ACH** — built into Stripe | Same integration, add `payment_method_types: ['us_bank_account']`. Lower fees (~0.8% vs 2.9%). |
| Send payment links (no integration needed) | **Stripe Payment Links** — create in dashboard, share URL | Generate a link → text/email it to client → they pay. Zero code for this path. |
| Milestone-based invoicing during construction | **Stripe Invoicing** — programmatic invoice creation + payment | Create invoice via API with line items → Stripe emails it → client pays online. Track in dashboard. |
| Client-facing proposal viewer with interactive 3D | **Custom shareable link** (your app) | `/proposal/{id}` — public page with 3D viewer + rendered images + estimate + CTA buttons (sign + pay). |

**Net result**: Design finalized → auto-generate proposal PDF + shareable link → email to client → client views interactive 3D, reviews proposal → signs digitally → pays deposit → all tracked in CRM. Zero manual steps.

---

### CRM & PIPELINE — "Track every lead and deal"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Contact management | **PostgreSQL** (custom, purpose-built for pool business) | contacts table: name, email, phone, address, source, division, lead_score, status, assigned_to. Tight integration with design pipeline. |
| Pipeline visualization | **React + any kanban library** (react-beautiful-dnd, dnd-kit) | Drag-and-drop pipeline: New → Contacted → Qualified → Proposal Sent → Signed → In Construction → Completed → Lost |
| Activity logging | **PostgreSQL activity_log table** + event-driven writes | Every action logged: design created, proposal sent, contract signed, email opened, call logged. Full audit trail. |
| Analytics dashboard | **Recharts** or **Tremor** (React charting) + PostgreSQL aggregations | Lead funnel, conversion rates, revenue by division, agent ROI, design popularity. Real-time from database. |
| Alternative: Use existing CRM | **HubSpot API** (free tier) or **Salesforce API** | If you want proven CRM features: use HubSpot/Salesforce as CRM, sync data bidirectionally via API. Your app feeds leads in, pulls pipeline out. |

---

### AGENTIC LEAD GENERATION — "Perpetual background prospecting"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Scan building permits for new home construction | **Shovels.ai API** — nationwide building permit data, AI-enriched, REST API | Query: `GET /permits?geo_id={zip}&type=new_construction&after={date}` → returns new home permits with addresses, builders, property details. Runs on a cron schedule. |
| Deeper permit data + property enrichment | **ATTOM Data API** — 158M properties, permit data, valuations | Enrich Shovels leads with: assessed value, lot size, structure sqft, ownership info. One API call per property. |
| Alternative permit data | **Construction Monitor API** — hourly updates, REST + FTP | Real-time permit feed. Redundancy for Shovels. |
| Find homes with big yards + no pool + high income | **ATTOM + Regrid + Google Maps** combination | ATTOM: filter by lot_size > 0.25 acres AND assessed_value > $400K AND no pool flag. Regrid: get parcel boundary. Google Maps: satellite image for visual confirmation. |
| Analyze satellite images for pool presence/condition | **Claude Vision API** (Anthropic) | Send aerial image → Claude analyzes: "This property has a pool approximately 15x30ft that appears to be in poor condition with discolored water and cracked decking. Good candidate for renovation." **Works NOW.** |
| Score and rank leads | **Claude API** + scoring formula | Property value × lot size × no-pool-flag × neighborhood income × proximity to service area = lead score. Claude can also reason about qualitative factors from image analysis. |
| Auto-generate concept proposals for top leads | **Your own design engine** (Pillar 2 + 3) | For each qualified lead: fetch property imagery → infer ideal pool config (based on yard size, home style, budget band) → generate 3D concept → calculate estimate → assemble proposal. Fully automated. |
| Email proposals to prospects | **AWS SES** — $0.10/1000 emails, production email delivery | Personalized HTML email with concept render, estimate range, CTA to view full interactive proposal. Triggered automatically by agent. |
| SMS outreach | **Twilio API** — $0.0079/SMS | "Hi [Name], we designed a pool concept for your property at [Address]. See it in 3D: [link]". Triggered on cadence Day 3. |
| Physical mail (postcards with pool render) | **Lob API** — programmatic direct mail, prints + mails for ~$0.65-1.20/piece | Send: recipient address + front/back HTML template with pool render image → Lob prints a full-color postcard → mails it via USPS. Triggered on cadence Day 7. **API call = physical mail arrives at their door.** |
| Email builders about new construction opportunities | **AWS SES** — same as above | Detect new construction permit → look up builder contact → send tailored proposal: "We noticed you're building at [address]. We'd love to be your pool partner." |
| Search MLS for homes that match criteria | **Zillow/Realtor API** or **ATTOM MLS data** or **Realty Mole API** | Filter: recently sold homes, large lots, pool-friendly neighborhoods. Identify buyers who just purchased homes with big yards. |
| Hotel/resort prospecting | **STR data** + **EagleView** + **Claude Vision** | Identify resort properties → analyze aerial imagery for aging pool areas → auto-generate concept renovation proposal → email property management. |

**Net result**: Agents run 24/7 in the background. They find properties, analyze them, score them, generate proposals with photorealistic renders of what a pool WOULD look like on their property, and deliver those proposals via email, text, and physical mail. You wake up to a CRM full of warm leads who've already seen what their pool could look like.

---

### ADMIN CHAT — "Tell agents to make changes"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Natural language chat interface | **Claude API** (Anthropic) with **tool use/function calling** | Claude receives your message → reasons about what to do → calls the right tool (update pricing, query CRM, generate design, scale infra, deploy code). |
| Multi-step agent orchestration | **LangGraph** (by LangChain) — production agent framework | Define agent workflow graphs: if user says "update pricing" → call pricing tool → confirm → apply. If "generate proposal" → chain: fetch property → design → render → assemble → send. |
| Infrastructure management via chat | **AWS SDK** (boto3 for Python, @aws-sdk for Node) | "Spin up 2 more GPU instances" → agent calls `ec2.run_instances()` with your pre-configured AMI. "Scale down render queue" → agent calls `ecs.update_service()`. |
| Code deployment via chat | **GitHub Actions API** | "Deploy latest to production" → agent triggers `workflow_dispatch` event on your GitHub Actions pipeline → CI/CD runs → deployed. |
| Database management via chat | **Prisma** or **direct PostgreSQL** queries | "Update PebbleTec Caribbean Blue to $14.50/sqft" → agent writes SQL: `UPDATE material_pricing SET unit_cost = 14.50 WHERE item_name = 'PebbleTec Caribbean Blue'`. Confirms before executing. |

---

### EMBEDDABLE WIDGET — "API on MizuByOrion.com"

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| Framework-agnostic embeddable component | **Lit** (by Google) — Web Components library | Build `<mizu-pool-designer>` as a Web Component with Shadow DOM. Works on any website regardless of framework. Single `<script>` tag to embed. |
| Alternative: iframe embed | **Standard HTML iframe** | Simplest path: host designer at `designer.mizubyorion.com` → embed via `<iframe src="...">`. Zero integration complexity. |
| API for licensee pool builders | **Express/Fastify REST API** behind **AWS API Gateway** | RESTful endpoints with API key auth, rate limiting per licensee, usage metering. API Gateway handles auth + throttling natively. |
| API key management + billing for licensees | **Stripe Billing** + **AWS API Gateway usage plans** | Licensee signs up → gets API key → Stripe charges monthly SaaS fee → API Gateway enforces rate limits per plan tier. All pre-built. |

---

### PAYMENT & LICENSING INFRASTRUCTURE

| What You Need | What Already Exists | How It Plugs In |
|---|---|---|
| SaaS subscription billing for licensees | **Stripe Billing** — recurring subscriptions, usage-based pricing, customer portal | Create products + plans → licensees subscribe → Stripe handles invoicing, dunning, upgrades, cancellations. Pre-built customer portal. |
| Marketplace for the widget/API | **Your own portal** or **AWS Marketplace** | Start with your own licensee portal (simpler). Graduate to AWS Marketplace when you want enterprise distribution. |

---

## THE PARALLEL EXECUTION PLAN

With your army of operators and agents, **all of these streams execute simultaneously.** Not sequentially. Here's how you deploy teams:

```
STREAM A: PROPERTY INTELLIGENCE        STREAM B: 3D ENGINE              STREAM C: CAD PIPELINE
┌─────────────────────────┐             ┌──────────────────────┐        ┌──────────────────────┐
│ Team/Agent A             │             │ Team/Agent B          │        │ Team/Agent C          │
│                         │             │                      │        │                      │
│ • EagleView API setup   │             │ • Three.js scene     │        │ • ezdxf plan          │
│ • Google Maps integration│             │ • Water shader       │        │   generation          │
│ • Regrid parcel API     │             │ • Pool geometry lib  │        │ • Plumbing schematic  │
│ • ATTOM property API    │             │ • Material system    │        │ • Electrical plan     │
│ • Survey upload parser  │             │ • Blender headless   │        │ • Equipment schedule  │
│ • Claude Vision extract │             │   render pipeline    │        │ • PDF assembly        │
│                         │             │ • React Three Fiber  │        │ • CadQuery geometry   │
│ DELIVERS:               │             │   viewer component   │        │                      │
│ Address → full property │             │                      │        │ DELIVERS:             │
│ context in <5 sec       │             │ DELIVERS:            │        │ Design → permit-ready │
└─────────────────────────┘             │ Config → live 3D +   │        │ plan set in <60 sec   │
                                        │ photorealistic render │        └──────────────────────┘
                                        └──────────────────────┘
                                        
STREAM D: COST ENGINE                   STREAM E: COMMERCE               STREAM F: LEAD GEN AGENTS
┌─────────────────────────┐             ┌──────────────────────┐        ┌──────────────────────┐
│ Team/Agent D             │             │ Team/Agent E          │        │ Team/Agent F          │
│                         │             │                      │        │                      │
│ • Equipment DB seed     │             │ • Stripe integration │        │ • Shovels.ai API     │
│   (Hayward/Pentair/Jandy)│             │ • DocuSign/DocuSeal  │        │ • ATTOM permit feed  │
│ • Material pricing DB   │             │ • Proposal PDF gen   │        │ • Claude Vision sat  │
│ • LTI list digitization │             │ • Client portal      │        │   image analysis     │
│ • Cost formula engine   │             │ • Embeddable widget  │        │ • Lead scoring       │
│ • Region multipliers    │             │ • API Gateway setup  │        │ • Auto-proposal gen  │
│ • Tile/deck catalog     │             │ • Licensee portal    │        │ • Email via SES      │
│                         │             │                      │        │ • SMS via Twilio     │
│ DELIVERS:               │             │ DELIVERS:            │        │ • Direct mail via Lob│
│ Any config → ±10% est   │             │ Full quote-to-cash   │        │ • Outreach cadence   │
│ in real-time             │             │ in one click         │        │                      │
└─────────────────────────┘             └──────────────────────┘        │ DELIVERS:            │
                                                                        │ Warm leads in CRM    │
STREAM G: INFRASTRUCTURE               STREAM H: ADMIN + CRM           │ every morning with   │
┌─────────────────────────┐             ┌──────────────────────┐        │ renders of THEIR     │
│ Team/Agent G             │             │ Team/Agent H          │        │ property already     │
│                         │             │                      │        │ attached             │
│ • AWS VPC + RDS + ECS   │             │ • Admin dashboard    │        └──────────────────────┘
│ • Cloudflare CDN + R2   │             │ • CRM module         │
│ • GitHub Actions CI/CD  │             │ • Agent chat (Claude)│
│ • Terraform IaC         │             │ • Analytics          │
│ • GPU instance config   │             │ • Pipeline views     │
│ • Secrets management    │             │ • Configurator UI    │
│                         │             │   (all 8 steps)      │
│ DELIVERS:               │             │                      │
│ Production infra Day 1  │             │ DELIVERS:            │
└─────────────────────────┘             │ Your command center  │
                                        └──────────────────────┘
```

**All 8 streams run simultaneously.** Each stream has a clear deliverable. Each stream's team/agents can work independently because the interfaces between streams are well-defined (JSON configs, REST APIs, database schemas).

---

## THE INTEGRATION POINTS (How Streams Connect)

```
                    ┌──────────────────────────────────────┐
                    │         CENTRAL DATABASE              │
                    │      (PostgreSQL + PostGIS)           │
                    │                                      │
                    │  contacts | designs | equipment      │
                    │  materials | proposals | activity    │
                    │  cost_formulas | render_queue        │
                    └──────────┬───────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
     Stream A output    Stream B reads     Stream F writes
     (property data)    (design config)    (new leads)
     writes to DB       from DB            to DB
            │                  │                  │
            └──────────────────┼──────────────────┘
                               │
                    ┌──────────▼───────────────────────────┐
                    │         MESSAGE QUEUE                 │
                    │         (AWS SQS)                     │
                    │                                      │
                    │  render_queue | email_queue           │
                    │  sms_queue | mail_queue               │
                    │  permit_scan_queue                    │
                    └──────────────────────────────────────┘
```

The database and message queue are the glue. Every stream reads from and writes to the same data layer. That's it. No complex inter-service dependencies.

---

## WHAT TO DO RIGHT NOW — PARALLEL ACTIONS

These are not sequential. Fire all of them today:

| # | Action | Who | Time to Complete |
|---|--------|-----|-----------------|
| 1 | Sign up for **EagleView Developer Portal** — get free Imagery API trial | You or operator | 15 minutes |
| 2 | Sign up for **Shovels.ai** — evaluate Gulf Coast permit data | You or operator | 15 minutes |
| 3 | Sign up for **ATTOM Data** developer account | Operator | 15 minutes |
| 4 | Sign up for **Regrid** developer account | Operator | 15 minutes |
| 5 | Create **Stripe** account (if not already) — enable Payments, Billing, Invoicing | Operator | 30 minutes |
| 6 | Create **DocuSign** developer account (free sandbox) | Operator | 15 minutes |
| 7 | Create **Lob** developer account (free tier) | Operator | 15 minutes |
| 8 | Create **Twilio** account — get SMS number | Operator | 15 minutes |
| 9 | Set up **AWS account** with org structure (dev/staging/prod) | Infrastructure agent | 2 hours |
| 10 | Set up **GitHub org** with monorepo scaffold | Dev agent | 1 hour |
| 11 | Seed **equipment database** from Hayward/Pentair/Jandy published catalogs | Data operator | 4-8 hours |
| 12 | Digitize your **LTI material pricing** list into structured data | Data operator | 4-8 hours |
| 13 | Build **Three.js pool proof-of-concept** — rectangle pool with water shader | 3D dev agent | 4-6 hours |
| 14 | Build **ezdxf plan generation** POC — generate site plan from parameters | CAD dev agent | 4-6 hours |
| 15 | Test **Claude Vision** on 5 real surveys — validate extraction accuracy | AI agent | 2 hours |
| 16 | Wire **EagleView → Google Maps → property display** pipeline | API dev agent | 4-6 hours |

**Every single one of these can happen on Day 1, in parallel.**

Items 1-8 are literally just signing up for accounts. Items 9-16 are independent work streams that don't block each other.

---

## THE REVENUE REALITY

This isn't a cost center. This is a **revenue weapon:**

| Channel | How It Makes Money |
|---------|-------------------|
| **Internal Mizu tool** | Faster proposals = more closes. A client sees a photorealistic render of their pool on their actual property within 24 hours of inquiry. Nobody else on the Gulf Coast can do that. |
| **API licensing** | Other pool builders pay $499-$2,999/mo to access this. There are ~60,000 pool builders in the US. Even 0.1% penetration = 60 licensees × $1,000/mo = $720K/yr recurring. |
| **White-label widget** | Premium builders pay $2,999-$4,999/mo for a fully branded version on their website. |
| **Per-render fees** | $5-25 per photorealistic render above tier allotment. Volume scales. |
| **Lead gen as a service** | Eventually: sell the prospecting engine to other builders who want the permit-scanning, satellite-analyzing, auto-proposing agents. |

---

## THE COMPETITIVE MOAT

No pool builder has this. Not Platinum Pools, not Cody Pools, not Premier Pools & Spas. The industry standard is: phone call → manual site visit → 2-week wait for hand-drawn design → Excel spreadsheet estimate. You're giving clients an interactive 3D experience on their actual property with a ±10% cost estimate before you ever leave your office. And your agents are finding the next 100 clients while you sleep.

**This is not a software project. This is a competitive weapon assembled from parts that already exist.**

---

## FULL SYSTEM ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              MIZU POOL DESIGNER                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  FRONTENDS                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │  MASTER ADMIN    │  │  CLIENT PORTAL   │  │  EMBEDDABLE WIDGET           │  │
│  │  (Next.js 15)    │  │  (Next.js 15)    │  │  (Lit Web Component)         │  │
│  │                  │  │                  │  │                              │  │
│  │  • Dashboard     │  │  • View proposal │  │  • Pool configurator         │  │
│  │  • CRM           │  │  • 3D viewer     │  │  • 3D preview                │  │
│  │  • Design studio │  │  • Sign contract │  │  • Instant estimate          │  │
│  │  • Agent chat    │  │  • Pay deposit   │  │  • Lead capture              │  │
│  │  • Cost manager  │  │  • Track project │  │                              │  │
│  │  • Agent control │  │                  │  │  Embeds on MizuByOrion.com   │  │
│  │  • Analytics     │  │                  │  │  + any licensee website      │  │
│  │  • API/license   │  │                  │  │                              │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────┬───────────────┘  │
│           │                     │                            │                  │
│           └─────────────────────┼────────────────────────────┘                  │
│                                 │                                                │
│                      ┌──────────▼──────────┐                                    │
│                      │    API GATEWAY      │                                    │
│                      │  (AWS API Gateway)  │                                    │
│                      │                     │                                    │
│                      │  • Auth (JWT/API key)│                                   │
│                      │  • Rate limiting     │                                   │
│                      │  • Usage metering    │                                   │
│                      │  • CORS             │                                    │
│                      └──────────┬──────────┘                                    │
│                                 │                                                │
│  ┌──────────────────────────────┼───────────────────────────────────────────┐   │
│  │                       CORE API SERVER                                    │   │
│  │                    (Node.js + TypeScript)                                │   │
│  │                    (ECS Fargate container)                               │   │
│  │                                                                          │   │
│  │  tRPC Router (internal)  +  REST endpoints (public/licensee API)        │   │
│  │                                                                          │   │
│  │  Routes:                                                                 │   │
│  │  /property/*     → Property Intelligence Service                        │   │
│  │  /design/*       → Design Engine Service                                │   │
│  │  /render/*       → Render Pipeline Service                              │   │
│  │  /estimate/*     → Cost Estimation Service                              │   │
│  │  /proposal/*     → Proposal & Commerce Service                          │   │
│  │  /crm/*          → CRM Service                                          │   │
│  │  /agents/*       → Agent Orchestration Service                          │   │
│  │  /admin/*        → Admin & Chat Service                                 │   │
│  │  /api/v1/*       → Public Licensee API                                  │   │
│  └──────────┬───────────────────┬────────────────────┬─────────────────────┘   │
│             │                   │                    │                          │
│  ┌──────────▼────────┐ ┌───────▼────────┐ ┌────────▼──────────┐               │
│  │  PYTHON SERVICES  │ │  GPU SERVICE   │ │  AGENT RUNTIME    │               │
│  │  (ECS Fargate)    │ │  (EC2 G5)      │ │  (ECS Fargate)    │               │
│  │                   │ │                │ │                   │               │
│  │  • CAD gen (ezdxf)│ │  • Blender     │ │  • Permit scanner │               │
│  │  • CadQuery       │ │    headless    │ │  • Property intel │               │
│  │  • Survey parse   │ │  • Cycles GPU  │ │  • Sat image anal │               │
│  │  • Hydraulic calc │ │    renderer    │ │  • Proposal gen   │               │
│  │  • Structural calc│ │  • Batch queue │ │  • Outreach orch  │               │
│  │  • PDF generation │ │    processing  │ │  • Lead scoring   │               │
│  └───────────────────┘ └────────────────┘ │  • Claude API     │               │
│                                            │  • LangGraph      │               │
│                                            └───────────────────┘               │
│                                                                                 │
│  DATA LAYER                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐  │   │
│  │  │ PostgreSQL  │  │ Redis        │  │ Cloudflare  │  │ AWS SQS      │  │   │
│  │  │ + PostGIS   │  │ (ElastiCache)│  │ R2 + S3     │  │ + EventBridge│  │   │
│  │  │             │  │              │  │             │  │              │  │   │
│  │  │ • Contacts  │  │ • Sessions   │  │ • 3D assets │  │ • Render Q   │  │   │
│  │  │ • Designs   │  │ • Rate limit │  │ • Renders   │  │ • Email Q    │  │   │
│  │  │ • Equipment │  │ • Preview    │  │ • CAD files │  │ • SMS Q      │  │   │
│  │  │ • Materials │  │   cache      │  │ • Proposals │  │ • Mail Q     │  │   │
│  │  │ • Proposals │  │ • API cache  │  │ • Textures  │  │ • Permit Q   │  │   │
│  │  │ • Activity  │  │              │  │ • Surveys   │  │ • Scan Q     │  │   │
│  │  │ • Permits   │  │              │  │             │  │              │  │   │
│  │  │ • Geo data  │  │              │  │ Zero egress │  │              │  │   │
│  │  └─────────────┘  └──────────────┘  └─────────────┘  └──────────────┘  │   │
│  │                                                                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  EXTERNAL APIS (all plug-and-play, all have production REST APIs)              │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                                                                          │   │
│  │  EagleView │ Google Maps │ Regrid │ ATTOM │ Shovels.ai │ Claude API    │   │
│  │  Stripe │ DocuSign │ Twilio │ Lob │ AWS SES │ Construction Monitor      │   │
│  │                                                                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## INFRASTRUCTURE STACK — EVERY SERVICE, EVERY PURPOSE

### Compute

| Service | Purpose | Config | Cost/mo |
|---------|---------|--------|---------|
| **AWS ECS Fargate** | Core API server, Python services, Agent runtime | 2 vCPU / 4GB per task, auto-scaling 2-10 tasks | $150-600 |
| **AWS EC2 G5.xlarge** | Blender GPU rendering (NVIDIA A10G) | Spot instances for batch, on-demand for priority | $400-2,000 |
| **AWS Lambda** | Lightweight event handlers (webhooks, cron triggers) | Per-invocation, auto-scales to zero | $10-50 |
| **Cloudflare Workers** | Edge logic: widget serving, API routing, geo-detection | Runs at 300+ edge locations globally | $5-25 |

### Data

| Service | Purpose | Config | Cost/mo |
|---------|---------|--------|---------|
| **AWS RDS PostgreSQL 16 + PostGIS** | Primary database: all business data + geospatial queries | db.r6g.large, Multi-AZ, 100GB SSD | $200-400 |
| **AWS ElastiCache Redis** | Session cache, API cache, render preview cache, rate limiting | cache.r6g.large, single node initially | $150-250 |
| **Cloudflare R2** | 3D assets, rendered images, CAD files, textures, surveys | Pay for storage only, **zero egress fees** | $50-200 |
| **AWS S3** | Backup storage, AWS-native integrations (Lambda triggers) | Standard tier | $25-50 |
| **AWS OpenSearch** | Full-text search: permits, CRM contacts, proposals, equipment | t3.small.search, 1 node | $75-150 |

### Networking & Security

| Service | Purpose | Config | Cost/mo |
|---------|---------|--------|---------|
| **Cloudflare Pro** | CDN, DDoS protection, WAF, SSL, caching | Pro plan | $20 |
| **AWS API Gateway** | REST API management, auth, rate limiting, usage plans | HTTP API type (cheaper than REST) | $10-50 |
| **AWS Secrets Manager** | API keys, credentials, encryption keys (auto-rotation) | Per-secret pricing | $10-20 |
| **AWS WAF** | Additional web application firewall rules | Attached to API Gateway | $10-30 |

### CI/CD & DevOps

| Service | Purpose | Cost/mo |
|---------|---------|---------|
| **GitHub Team** | Private repos, Actions CI/CD, code review | $4/user |
| **Terraform Cloud** | Infrastructure state management, plan/apply workflow | Free tier (up to 5 users) |
| **AWS CloudWatch** | Logs, metrics, alarms, dashboards | $25-75 |
| **Sentry** | Error tracking, performance monitoring | $26/mo (Team) |

### Total Infrastructure Cost: $1,200 - $3,900/mo starting, scaling with usage

---

## COMPLETE DATABASE SCHEMA

### Core Tables

```sql
-- ═══════════════════════════════════════════════════════════
-- CONTACTS & CRM
-- ═══════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(200),
    phone VARCHAR(20),
    company VARCHAR(200),                 -- for builders, hotels
    
    -- Address
    address_line1 VARCHAR(200),
    address_line2 VARCHAR(100),
    city VARCHAR(100),
    state VARCHAR(2),
    zip VARCHAR(10),
    country VARCHAR(2) DEFAULT 'US',
    location GEOGRAPHY(POINT, 4326),     -- PostGIS point for geo queries
    
    -- Classification
    contact_type VARCHAR(20) NOT NULL DEFAULT 'residential',  
        -- 'residential', 'builder', 'hotel', 'resort', 'architect', 'licensee'
    division VARCHAR(30),                 -- 'signature', 'ultra_luxury', 'hospitality'
    source VARCHAR(50) NOT NULL,          
        -- 'widget', 'website_form', 'agent_permit_scan', 'agent_property_scan',
        -- 'agent_satellite_scan', 'referral', 'manual', 'api_licensee'
    source_detail JSONB,                  -- {"agent": "permit_scanner", "permit_id": "xxx", "scan_date": "..."}
    
    -- Scoring & Status
    lead_score INTEGER DEFAULT 0,         -- 0-100, AI-calculated
    status VARCHAR(30) NOT NULL DEFAULT 'new',
        -- 'new', 'contacted', 'engaged', 'qualified', 'proposal_sent', 
        -- 'proposal_viewed', 'negotiating', 'signed', 'deposit_paid',
        -- 'in_construction', 'completed', 'lost', 'dormant'
    lost_reason VARCHAR(200),
    
    -- Assignment
    assigned_to UUID,                     -- internal user ID
    
    -- Metadata
    tags TEXT[],
    notes TEXT,
    custom_fields JSONB,
    
    -- Property Intelligence (cached from APIs)
    property_data JSONB,                  -- ATTOM data, lot size, home value, etc.
    aerial_image_urls JSONB,              -- EagleView/Google cached URLs
    parcel_boundary GEOGRAPHY(POLYGON, 4326),  -- PostGIS polygon from Regrid
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_contacts_status ON contacts(status);
CREATE INDEX idx_contacts_division ON contacts(division);
CREATE INDEX idx_contacts_lead_score ON contacts(lead_score DESC);
CREATE INDEX idx_contacts_location ON contacts USING GIST(location);
CREATE INDEX idx_contacts_source ON contacts(source);

-- ═══════════════════════════════════════════════════════════
-- DESIGNS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE designs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    
    -- Design identity
    name VARCHAR(200),                    -- "Smith Residence Pool" or auto-generated
    division VARCHAR(30) NOT NULL,
    version INTEGER DEFAULT 1,
    parent_design_id UUID REFERENCES designs(id),  -- for version tracking
    
    -- The full configuration (this is THE design)
    configuration JSONB NOT NULL,         -- complete design config (see Configuration Schema below)
    
    -- Property context
    property_address TEXT,
    property_location GEOGRAPHY(POINT, 4326),
    property_data JSONB,                  -- cached property intelligence
    survey_file_url TEXT,
    survey_extracted_data JSONB,          -- Claude Vision extraction results
    
    -- Generated outputs
    scene_data JSONB,                     -- 3D scene graph for Three.js viewer
    model_url TEXT,                       -- glTF model file URL (R2)
    
    -- Estimates
    estimate_low DECIMAL(12,2),
    estimate_high DECIMAL(12,2),
    estimate_breakdown JSONB,             -- itemized cost breakdown
    
    -- Status
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
        -- 'draft', 'configured', 'rendering', 'rendered', 'proposed', 
        -- 'revision_requested', 'approved', 'in_construction', 'completed'
    
    -- Render tracking
    render_job_ids TEXT[],                -- SQS job IDs for queued renders
    render_urls JSONB,                    -- {"aerial": "url", "eye_level": "url", "twilight": "url", ...}
    
    -- CAD tracking
    cad_job_id TEXT,
    cad_files JSONB,                      -- {"site_plan_dxf": "url", "pool_plan_pdf": "url", ...}
    
    -- Metadata
    created_by VARCHAR(50),               -- 'admin', 'widget_user', 'agent_auto', 'api_licensee:{id}'
    licensee_id UUID,                     -- if created by API licensee
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_designs_contact ON designs(contact_id);
CREATE INDEX idx_designs_status ON designs(status);
CREATE INDEX idx_designs_division ON designs(division);

-- ═══════════════════════════════════════════════════════════
-- EQUIPMENT CATALOG
-- ═══════════════════════════════════════════════════════════

CREATE TABLE equipment_catalog (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Identity
    brand VARCHAR(50) NOT NULL,           -- 'hayward', 'pentair', 'jandy'
    category VARCHAR(50) NOT NULL,        
        -- 'pump', 'filter', 'heater', 'salt_chlorinator', 'automation',
        -- 'cleaner', 'light', 'uv_ozone', 'chemical_feeder', 'valve',
        -- 'blower', 'booster_pump'
    subcategory VARCHAR(50),              -- 'variable_speed', 'single_speed', 'cartridge', 'de', 'sand'
    
    model_name VARCHAR(200) NOT NULL,     -- "IntelliFlo3 VSF"
    model_number VARCHAR(100),            -- "011056"
    
    -- Specifications (varies by category)
    specifications JSONB NOT NULL,
    -- Pump: {"hp": 3.0, "voltage": "230V", "speed_type": "variable", "max_flow_gpm": 160, "max_head_ft": 65, "wef": 11.2}
    -- Filter: {"type": "cartridge", "sqft": 520, "max_flow_gpm": 150, "pipe_size": "2in"}
    -- Heater: {"type": "gas", "btu": 400000, "fuel": "natural_gas", "efficiency_pct": 84}
    -- Automation: {"max_devices": 40, "has_app": true, "alexa": true, "google_home": true}
    -- Light: {"type": "led", "watts": 40, "color_modes": 16, "voltage": "12V", "niche": "standard"}
    -- Salt: {"max_gallons": 40000, "chlorine_output_lbs": 1.45}
    
    -- Pricing
    msrp DECIMAL(10,2),
    dealer_cost DECIMAL(10,2),
    install_labor_hours DECIMAL(5,2),
    install_labor_cost DECIMAL(8,2),      -- pre-calculated based on labor rate
    
    -- Compatibility
    compatible_brands TEXT[],             -- which other brands it works with
    compatible_categories JSONB,          -- {"automation": ["OmniLogic", "IntelliCenter"]}
    requires TEXT[],                      -- other equipment required (e.g., booster pump for pressure cleaner)
    
    -- Media
    image_url TEXT,
    thumbnail_url TEXT,
    datasheet_url TEXT,
    
    -- Sizing rules (for auto-recommendation)
    sizing_rules JSONB,
    -- {"min_pool_gallons": 10000, "max_pool_gallons": 40000, "recommended_for": "residential"}
    
    -- Admin
    active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_equipment_brand ON equipment_catalog(brand);
CREATE INDEX idx_equipment_category ON equipment_catalog(category);
CREATE INDEX idx_equipment_active ON equipment_catalog(active);

-- ═══════════════════════════════════════════════════════════
-- MATERIAL PRICING
-- ═══════════════════════════════════════════════════════════

CREATE TABLE material_pricing (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Classification
    category VARCHAR(100) NOT NULL,
        -- 'plaster', 'pebble_finish', 'quartz_finish', 'glass_bead',
        -- 'waterline_tile', 'full_tile', 'mosaic', 'coping', 'paver',
        -- 'travertine', 'natural_stone', 'concrete_stamped', 'concrete_exposed',
        -- 'rebar', 'gunite', 'shotcrete', 'plumbing_pipe', 'plumbing_fitting',
        -- 'electrical_conduit', 'electrical_wire', 'excavation', 'haul_off',
        -- 'backfill', 'gravel', 'expansion_joint', 'waterproofing', 'bond_beam'
    subcategory VARCHAR(100),             -- 'PebbleTec Caribbean Blue', '2x2 Porcelain', etc.
    item_name VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(200),
    sku VARCHAR(100),
    
    -- Pricing
    unit VARCHAR(20) NOT NULL,            -- 'sqft', 'lnft', 'cuyd', 'each', 'ton', 'lb', 'ft', 'hr'
    material_cost_per_unit DECIMAL(10,2) NOT NULL,
    labor_cost_per_unit DECIMAL(10,2) DEFAULT 0,
    total_installed_cost_per_unit DECIMAL(10,2) GENERATED ALWAYS AS 
        (material_cost_per_unit + labor_cost_per_unit) STORED,
    
    -- Regional pricing
    region VARCHAR(50) DEFAULT 'gulf_coast',  -- 'gulf_coast', 'southeast', 'national', etc.
    
    -- Media (for catalog display)
    image_url TEXT,
    thumbnail_url TEXT,
    color_hex VARCHAR(7),                 -- for color swatches in UI
    
    -- Validity
    effective_date DATE DEFAULT CURRENT_DATE,
    expires_date DATE,
    supplier VARCHAR(200),
    
    -- Admin
    active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_materials_category ON material_pricing(category);
CREATE INDEX idx_materials_region ON material_pricing(region);
CREATE INDEX idx_materials_active ON material_pricing(active);

-- ═══════════════════════════════════════════════════════════
-- COST FORMULAS (for features that have complex pricing)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE cost_formulas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    feature_key VARCHAR(100) NOT NULL UNIQUE,
        -- 'infinity_edge', 'raised_spa', 'spillover_spa', 'grotto',
        -- 'natural_waterfall', 'sheer_descent', 'deck_jets', 'fire_bowl',
        -- 'sun_shelf', 'beach_entry', 'outdoor_kitchen', 'pergola',
        -- 'fire_pit', 'seating_wall', 'retaining_wall', 'in_floor_cleaning'
    
    display_name VARCHAR(200) NOT NULL,
    
    -- Base cost (fixed component)
    base_cost DECIMAL(12,2) DEFAULT 0,
    
    -- Variable cost
    per_unit_cost DECIMAL(10,2) DEFAULT 0,
    unit VARCHAR(20),                     -- 'lnft', 'sqft', 'each', etc.
    
    -- Multipliers (JSON rules)
    multiplier_conditions JSONB,
    -- Example for infinity_edge:
    -- {
    --   "height_over_4ft": 1.20,
    --   "curved": 1.15,
    --   "knife_edge": 1.30,
    --   "difficult_access": 1.25,
    --   "engineered_soil": 1.10
    -- }
    
    -- Division-specific overrides
    division_overrides JSONB,
    -- {"signature": {"margin_pct": 0.25}, "ultra_luxury": {"margin_pct": 0.35}}
    
    notes TEXT,
    active BOOLEAN DEFAULT true,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════
-- PROPOSALS & CONTRACTS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE proposals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    design_id UUID NOT NULL REFERENCES designs(id),
    contact_id UUID NOT NULL REFERENCES contacts(id),
    
    -- Content
    proposal_pdf_url TEXT,
    proposal_html_url TEXT,               -- shareable interactive link
    cover_letter TEXT,
    terms_and_conditions TEXT,
    payment_schedule JSONB,
    -- [{"milestone": "Contract Signing", "pct": 10, "amount": 15000},
    --  {"milestone": "Excavation Complete", "pct": 25, "amount": 37500}, ...]
    
    -- Pricing
    total_price DECIMAL(12,2),
    discount_amount DECIMAL(10,2) DEFAULT 0,
    discount_reason VARCHAR(200),
    
    -- Tracking
    sent_at TIMESTAMPTZ,
    sent_via VARCHAR(20),                 -- 'email', 'sms', 'in_app'
    viewed_at TIMESTAMPTZ,
    view_count INTEGER DEFAULT 0,
    
    -- Signature
    signature_provider VARCHAR(50),       -- 'docusign', 'docuseal', 'dropbox_sign'
    signature_envelope_id VARCHAR(200),
    signed_at TIMESTAMPTZ,
    signer_ip VARCHAR(45),
    signature_data JSONB,
    
    -- Payment
    deposit_amount DECIMAL(10,2),
    deposit_stripe_payment_intent_id VARCHAR(200),
    deposit_paid_at TIMESTAMPTZ,
    deposit_payment_method VARCHAR(20),   -- 'card', 'ach'
    
    -- Status
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
        -- 'draft', 'sent', 'viewed', 'signed', 'deposit_paid', 
        -- 'active', 'completed', 'cancelled', 'expired'
    expires_at TIMESTAMPTZ,
    
    -- Versioning
    version INTEGER DEFAULT 1,
    previous_proposal_id UUID REFERENCES proposals(id),
    revision_notes TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════
-- ACTIVITY LOG (everything that happens, ever)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE activity_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Who/what
    contact_id UUID REFERENCES contacts(id),
    design_id UUID REFERENCES designs(id),
    proposal_id UUID REFERENCES proposals(id),
    
    -- What happened
    activity_type VARCHAR(50) NOT NULL,
        -- 'lead_created', 'lead_scored', 'lead_assigned',
        -- 'contact_email_sent', 'contact_sms_sent', 'contact_mail_sent',
        -- 'contact_email_opened', 'contact_email_clicked',
        -- 'design_created', 'design_updated', 'design_finalized',
        -- 'render_requested', 'render_completed',
        -- 'cad_generated', 'estimate_calculated',
        -- 'proposal_created', 'proposal_sent', 'proposal_viewed',
        -- 'contract_signed', 'deposit_paid', 'payment_received',
        -- 'call_logged', 'note_added', 'status_changed',
        -- 'agent_action', 'agent_permit_found', 'agent_property_analyzed',
        -- 'agent_proposal_generated', 'agent_outreach_sent'
    
    description TEXT,
    metadata JSONB,                       -- activity-specific data
    
    -- Who did it
    actor_type VARCHAR(20) NOT NULL,      -- 'admin', 'system', 'agent', 'client', 'api'
    actor_id VARCHAR(100),                -- user ID or agent name
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_activity_contact ON activity_log(contact_id);
CREATE INDEX idx_activity_type ON activity_log(activity_type);
CREATE INDEX idx_activity_created ON activity_log(created_at DESC);

-- ═══════════════════════════════════════════════════════════
-- RENDER JOBS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE render_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    design_id UUID NOT NULL REFERENCES designs(id),
    
    -- Config
    render_type VARCHAR(30) NOT NULL,     -- 'preview', 'final', 'panorama_360', 'video_turntable'
    camera_preset VARCHAR(30),            -- 'aerial', 'eye_level', 'twilight', 'underwater', 'custom'
    camera_config JSONB,                  -- position, target, FOV, etc.
    render_settings JSONB,                -- samples, resolution, output format
    
    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
        -- 'queued', 'processing', 'completed', 'failed'
    sqs_message_id VARCHAR(200),
    
    -- Results
    output_url TEXT,                       -- final rendered image URL
    render_time_seconds INTEGER,
    gpu_instance_id VARCHAR(50),
    
    -- Priority
    priority INTEGER DEFAULT 5,           -- 1=highest, 10=lowest
    
    -- Error handling
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- ═══════════════════════════════════════════════════════════
-- LEAD GEN: PERMITS & PROSPECTS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE permit_leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Source
    source_provider VARCHAR(50) NOT NULL,  -- 'shovels', 'attom', 'construction_monitor'
    external_permit_id VARCHAR(200),
    
    -- Permit data
    permit_type VARCHAR(50),              -- 'new_construction', 'renovation', 'pool', 'addition'
    permit_status VARCHAR(50),
    permit_date DATE,
    permit_value DECIMAL(12,2),
    
    -- Property
    property_address TEXT,
    property_location GEOGRAPHY(POINT, 4326),
    property_data JSONB,                  -- enriched property attributes
    
    -- Builder (for new construction)
    builder_name VARCHAR(200),
    builder_contact JSONB,
    
    -- Owner
    owner_name VARCHAR(200),
    owner_contact JSONB,                  -- from data enrichment
    
    -- AI Analysis
    lead_score INTEGER,
    analysis JSONB,                       -- Claude's analysis of the property
    satellite_image_url TEXT,
    has_pool BOOLEAN,
    pool_condition VARCHAR(30),           -- 'none', 'new', 'good', 'fair', 'poor', 'abandoned'
    yard_size_estimate VARCHAR(30),       -- 'small', 'medium', 'large', 'very_large'
    
    -- Processing status
    status VARCHAR(30) DEFAULT 'new',
        -- 'new', 'analyzed', 'qualified', 'proposal_generated', 
        -- 'outreach_sent', 'converted_to_contact', 'disqualified'
    contact_id UUID REFERENCES contacts(id),  -- if converted
    
    -- Outreach tracking
    outreach_history JSONB,
    -- [{"channel": "email", "sent_at": "...", "template": "new_construction"},
    --  {"channel": "sms", "sent_at": "...", "delivered": true},
    --  {"channel": "direct_mail", "sent_at": "...", "lob_id": "psc_xxx"}]
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_permits_status ON permit_leads(status);
CREATE INDEX idx_permits_location ON permit_leads USING GIST(property_location);
CREATE INDEX idx_permits_score ON permit_leads(lead_score DESC);
CREATE INDEX idx_permits_date ON permit_leads(permit_date DESC);

-- ═══════════════════════════════════════════════════════════
-- API LICENSEES
-- ═══════════════════════════════════════════════════════════

CREATE TABLE licensees (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    company_name VARCHAR(200) NOT NULL,
    contact_name VARCHAR(200),
    email VARCHAR(200) NOT NULL,
    phone VARCHAR(20),
    website VARCHAR(300),
    
    -- API access
    api_key_hash VARCHAR(200) NOT NULL,   -- bcrypt hashed API key
    api_key_prefix VARCHAR(10),           -- first 8 chars for identification "mizu_xxxx"
    plan_tier VARCHAR(20) NOT NULL,       -- 'basic', 'pro', 'enterprise'
    
    -- Stripe
    stripe_customer_id VARCHAR(200),
    stripe_subscription_id VARCHAR(200),
    
    -- Usage
    monthly_design_limit INTEGER,
    monthly_render_limit INTEGER,
    current_month_designs INTEGER DEFAULT 0,
    current_month_renders INTEGER DEFAULT 0,
    
    -- White-label config
    branding JSONB,
    -- {"company_name": "ABC Pools", "logo_url": "...", "primary_color": "#xxx", 
    --  "domain": "designer.abcpools.com"}
    
    -- Service area restriction
    service_areas JSONB,                  -- GeoJSON polygons or zip code lists
    
    -- Status
    status VARCHAR(20) DEFAULT 'active',
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## DESIGN CONFIGURATION SCHEMA (The Entire Configurator Data Model)

This is the JSON structure that represents a complete pool design. Every UI selection writes to this config. Every output (3D, CAD, estimate) reads from this config.

```typescript
// /types/design-configuration.ts

interface DesignConfiguration {
  version: string;  // "1.0.0"
  
  // ══════════════════════════════════════════════════
  // STEP 1: PROPERTY CONTEXT
  // ══════════════════════════════════════════════════
  property: {
    address: string;
    lat: number;
    lng: number;
    lot_width_ft: number;
    lot_depth_ft: number;
    lot_area_sqft: number;
    parcel_boundary: GeoJSON.Polygon;
    
    setbacks: {
      front_ft: number;
      rear_ft: number;
      left_side_ft: number;
      right_side_ft: number;
    };
    
    buildable_zone: GeoJSON.Polygon;      // lot minus setbacks
    
    existing_structures: {
      home_footprint: GeoJSON.Polygon;
      other_structures: GeoJSON.Polygon[];  // shed, garage, etc.
    };
    
    terrain: {
      grade_change_ft: number;            // max elevation change across buildable zone
      slope_direction: 'flat' | 'front_to_back' | 'back_to_front' | 'left_to_right' | 'right_to_left';
      soil_type: 'sand' | 'clay' | 'rock' | 'fill' | 'mixed' | 'unknown';
    };
    
    utilities: {
      gas_available: boolean;
      gas_type: 'natural_gas' | 'propane' | 'none';
      electrical_service_amps: number;
      water_source: 'municipal' | 'well';
    };
    
    pool_placement: {
      center_x: number;                   // relative to lot origin
      center_y: number;
      rotation_degrees: number;
    };
    
    survey_data?: {
      file_url: string;
      extracted: boolean;
      extraction_results: Record<string, any>;
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 2: POOL CONFIGURATION
  // ══════════════════════════════════════════════════
  pool: {
    shape: {
      type: 'rectangle' | 'l_shape' | 't_shape' | 'lazy_l' | 'roman' | 'grecian' |
            'kidney' | 'lagoon' | 'figure_8' | 'freeform' | 'custom';
      custom_outline?: GeoJSON.Polygon;   // for freeform/custom shapes
      
      // Geometric parameters (for standard shapes)
      length_ft: number;
      width_ft: number;
      corner_radius_ft?: number;          // for rounded corners
      
      // L-shape / T-shape specific
      secondary_length_ft?: number;
      secondary_width_ft?: number;
      offset_ft?: number;
    };
    
    dimensions: {
      perimeter_ft: number;               // auto-calculated
      surface_area_sqft: number;          // auto-calculated
      volume_gallons: number;             // auto-calculated from shape + depth
    };
    
    depth: {
      shallow_end_ft: number;             // 3.0 - 5.0 typical
      deep_end_ft: number;               // 5.0 - 12.0 typical
      profile: 'constant' | 'gradual_slope' | 'sport_bottom' | 'hopper_bottom';
      
      dive_well: {
        enabled: boolean;
        depth_ft: number;                 // 8, 9, 10, 12
        diameter_ft: number;
      };
      
      beach_entry: {
        enabled: boolean;
        slope_angle_degrees: number;      // 5-15 typical
        length_ft: number;
        width_ft: number;
      };
    };
    
    construction_type: 'gunite' | 'shotcrete' | 'fiberglass' | 'vinyl_liner';
    
    structural: {
      raised_walls: {
        enabled: boolean;
        locations: ('north' | 'south' | 'east' | 'west')[];
        height_ft: number;
        material: 'stone_veneer' | 'stucco' | 'tile' | 'natural_stone' | 'concrete';
        finish: string;
      };
      retaining_walls: {
        enabled: boolean;
        locations: Array<{
          side: string;
          height_ft: number;
          length_ft: number;
          engineered: boolean;
        }>;
      };
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 3: SPA / HOT TUB
  // ══════════════════════════════════════════════════
  spa: {
    enabled: boolean;
    
    type: 'attached' | 'detached' | 'perimeter_overflow';
    capacity: 2 | 4 | 6 | 8 | 'custom';
    custom_dimensions?: { length_ft: number; width_ft: number; };
    
    features: {
      raised: {
        enabled: boolean;
        height_above_deck_inches: number; // 12, 18, 24
      };
      
      spillover: {
        enabled: boolean;
        type: 'knife_edge' | 'weir' | 'natural_stone' | 'negative_edge' | 'scupper';
        width_ft: number;
      };
      
      jets: {
        therapy_jets: number;             // 0-8
        turbo_jets: number;               // 0-4
        directional_jets: number;         // 0-6
        air_blower: boolean;
      };
      
      bench_seating: {
        full_perimeter: boolean;
        partial_locations: string[];
        lounger_seats: number;            // 0-2 full-body lounger positions
      };
      
      independent_temperature: boolean;
      independent_filtration: boolean;
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 4: WATER FEATURES
  // ══════════════════════════════════════════════════
  water_features: {
    
    infinity_edge: {
      enabled: boolean;
      walls: Array<{
        side: 'north' | 'south' | 'east' | 'west';
        length_ft: number;
        type: 'knife_edge' | 'slot_overflow' | 'perimeter_overflow';
        catch_basin: 'below_grade_trough' | 'separate_pool';
      }>;
    };
    
    waterfalls: Array<{
      type: 'natural_rock' | 'sheer_descent' | 'scupper' | 'rain_curtain';
      height_ft: number;
      width_ft: number;
      
      // Natural rock specific
      rock_type?: 'moss_rock' | 'flagstone' | 'boulders' | 'stacked_stone';
      
      // Sheer descent specific
      projection_inches?: number;
      led_backlit?: boolean;
      led_color?: 'white' | 'blue' | 'color_changing';
      
      // Scupper specific
      material?: 'copper' | 'stainless' | 'bronze' | 'stone';
      quantity?: number;
      
      // Rain curtain specific
      led_options?: string;
    }>;
    
    grottos: {
      enabled: boolean;
      depth_ft: number;
      width_ft: number;
      height_ft: number;
      seating: boolean;
      lighting: boolean;
    };
    
    fountains: {
      deck_jets: {
        enabled: boolean;
        quantity: number;                  // 2-12 typical
        arc_height_ft: number;
        led_color: boolean;
        laminar: boolean;                  // smooth glass-like stream
      };
      
      bubblers: {
        enabled: boolean;
        quantity: number;
        placement: 'sun_shelf' | 'pool_floor' | 'steps' | 'custom';
        locations: Array<{ x: number; y: number; }>;
      };
      
      fire_water_bowls: {
        enabled: boolean;
        quantity: number;
        fuel: 'natural_gas' | 'propane';
        bowl_material: 'copper' | 'concrete' | 'stone' | 'stainless';
        bowl_size_inches: 24 | 30 | 36 | 48;
        fire_glass_color: string;
        water_spillover: boolean;         // fire + water bowl
      };
      
      spray_features: Array<{
        type: 'geyser' | 'spray_arc' | 'finger_jet' | 'mushroom';
        quantity: number;
      }>;
    };
    
    sun_shelf: {
      enabled: boolean;
      depth_inches: 6 | 8 | 10 | 12;
      area_sqft: number;
      shape: 'rectangle' | 'freeform' | 'curved';
      bubblers_on_shelf: {
        enabled: boolean;
        quantity: number;
      };
      furniture: {
        ledge_loungers: number;           // 0-4
        umbrella_sleeve: boolean;
        side_table: boolean;
      };
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 5: POOL INTERIOR FINISH
  // ══════════════════════════════════════════════════
  interior_finish: {
    type: 'standard_plaster' | 'colored_plaster' | 'pebble' | 'quartz' | 'glass_bead' | 'full_tile';
    
    // Plaster options
    plaster_color?: string;
    
    // Pebble options
    pebble_brand?: 'PebbleTec' | 'PebbleSheen' | 'PebbleFina';
    pebble_color?: string;                // "Caribbean Blue", "Midnight Blue", etc.
    
    // Quartz options
    quartz_brand?: 'QuartzScapes' | 'Diamond Brite';
    quartz_color?: string;
    
    // Glass bead
    glass_bead_color?: string;
    
    // Tile
    waterline_tile: {
      enabled: boolean;
      material_id: string;                // references material_pricing.id
      material: 'porcelain' | 'glass' | 'stone' | 'ceramic';
      size: '1x1' | '2x2' | '3x3' | '6x6' | 'mosaic';
      pattern: 'straight' | 'stacked' | 'herringbone' | 'mixed';
      color: string;
      rows: number;                       // 1-3 rows typical
    };
    
    full_interior_tile?: {
      material_id: string;
      material: string;
      pattern: string;
    };
    
    mosaic_accents?: Array<{
      placement: 'floor' | 'wall' | 'steps' | 'bench';
      design: 'custom' | 'medallion' | 'border';
      area_sqft: number;
    }>;
    
    special: {
      fiber_optic_floor: boolean;
      custom_led_integration: boolean;
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 6: DECK & HARDSCAPE
  // ══════════════════════════════════════════════════
  deck: {
    material: {
      type: 'pavers' | 'travertine' | 'stamped_concrete' | 'exposed_aggregate' | 
            'natural_stone' | 'wood_composite' | 'cool_deck';
      material_id: string;                // references material_pricing.id
      color: string;
      pattern: string;
      size?: string;                      // "12x24", "6x12", etc.
    };
    
    area_sqft: number;
    
    coping: {
      type: 'bullnose' | 'cantilevered' | 'flush_mount' | 'natural_stone' | 'paver';
      material_id: string;
      color: string;
    };
    
    outdoor_living: {
      outdoor_kitchen: {
        enabled: boolean;
        size: 'compact' | 'standard' | 'large' | 'custom';
        length_ft?: number;
        appliances: ('grill' | 'burner' | 'sink' | 'refrigerator' | 'ice_maker' | 
                     'pizza_oven' | 'smoker' | 'kegerator' | 'warming_drawer')[];
        countertop_material: string;
        base_material: 'stone_veneer' | 'stucco' | 'brick' | 'tile';
        has_bar_seating: boolean;
        bar_seats: number;
      };
      
      pergola: {
        enabled: boolean;
        size_sqft: number;
        material: 'wood_cedar' | 'wood_redwood' | 'aluminum' | 'vinyl' | 'steel';
        roof_type: 'open_slat' | 'louvered' | 'solid_roof' | 'retractable_canopy';
        attached_to_house: boolean;
        fan: boolean;
        lighting: boolean;
      };
      
      fire_feature: {
        enabled: boolean;
        type: 'fire_pit' | 'fireplace' | 'fire_table';
        fuel: 'natural_gas' | 'propane' | 'wood_burning';
        shape?: 'round' | 'square' | 'rectangle' | 'linear';
        material: 'stone' | 'concrete' | 'brick' | 'stucco';
        size_inches?: number;
      };
      
      seating_walls: Array<{
        length_ft: number;
        height_inches: number;            // 18-24 typical
        material: string;
        cap_material: string;
        has_lighting: boolean;
      }>;
      
      landscape_zones: Array<{
        type: 'planting_bed' | 'lawn' | 'gravel' | 'artificial_turf';
        area_sqft: number;
      }>;
    };
  };

  // ══════════════════════════════════════════════════
  // STEP 7: EQUIPMENT CONFIGURATION
  // ══════════════════════════════════════════════════
  equipment: {
    brand_preference: 'hayward' | 'pentair' | 'jandy' | 'mixed';
    
    pump: {
      equipment_id: string;               // references equipment_catalog.id
      brand: string;
      model: string;
      hp: number;
      speed_type: 'variable' | 'dual' | 'single';
      quantity: number;                    // usually 1, 2 for pool+spa separate
    };
    
    filter: {
      equipment_id: string;
      type: 'cartridge' | 'de' | 'sand';
      model: string;
      sqft_capacity: number;
    };
    
    heater: {
      equipment_id: string;
      type: 'gas' | 'heat_pump' | 'solar' | 'hybrid';
      btu: number;
      fuel?: 'natural_gas' | 'propane';
    };
    
    sanitization: {
      primary: {
        equipment_id: string;
        type: 'salt_chlorinator' | 'traditional_chlorine' | 'uv' | 'ozone';
        model: string;
      };
      secondary?: {
        equipment_id: string;
        type: 'uv' | 'ozone';
        model: string;
      };
    };
    
    automation: {
      equipment_id: string;
      brand: string;
      model: string;
      max_devices: number;
      smart_home: {
        alexa: boolean;
        google_home: boolean;
        apple_homekit: boolean;
      };
    };
    
    cleaner: {
      type: 'in_floor' | 'robotic' | 'suction' | 'pressure';
      equipment_id?: string;              // for robotic/suction/pressure
      model?: string;
      
      // In-floor specific
      in_floor_system?: 'paramount_pcc2000' | 'aa_quikclean' | 'caretaker';
      in_floor_head_count?: number;
      
      booster_pump_required?: boolean;
      booster_pump_id?: string;
    };
    
    lighting: {
      pool_lights: Array<{
        equipment_id: string;
        type: 'led_color' | 'led_white' | 'fiber_optic';
        quantity: number;
        placement: 'wall' | 'floor' | 'step' | 'niche';
      }>;
      
      spa_lights: Array<{
        equipment_id: string;
        quantity: number;
      }>;
      
      water_feature_lights: Array<{
        feature: string;                  // which water feature
        equipment_id: string;
        quantity: number;
      }>;
      
      deck_landscape_lights: {
        enabled: boolean;
        zones: number;
        fixtures_per_zone: number;
        type: 'led_path' | 'led_spot' | 'led_strip' | 'fiber_optic';
      };
      
      automation_color_shows: boolean;
      scheduling: boolean;
    };
    
    additional: {
      automatic_water_leveler: boolean;
      freeze_protection: boolean;
      overflow_drain: boolean;
      pool_cover: {
        enabled: boolean;
        type: 'manual' | 'automatic' | 'safety';
        material: 'mesh' | 'solid' | 'retractable';
      };
    };
  };

  // ══════════════════════════════════════════════════
  // METADATA
  // ══════════════════════════════════════════════════
  metadata: {
    created_at: string;
    updated_at: string;
    created_by: string;
    division: 'signature' | 'ultra_luxury' | 'hospitality';
    licensee_id?: string;
  };
}
```

---

## COST ESTIMATION ENGINE — COMPLETE BREAKDOWN

When the configuration changes, the cost engine runs every module simultaneously:

```
COST MODULE MAP
═══════════════════════════════════════════════════════════════

MODULE 1: EXCAVATION
├── pool_volume_cuyd = (pool.dimensions.volume_gallons / 202)
├── overdig_factor = 1.15 (15% overdig standard)
├── total_dig_cuyd = pool_volume_cuyd × overdig_factor
├── dig_cost = total_dig_cuyd × material_pricing['excavation'].cost_per_cuyd
├── haul_off_cuyd = total_dig_cuyd × 1.3 (swell factor)
├── haul_off_cost = haul_off_cuyd × material_pricing['haul_off'].cost_per_cuyd
├── access_multiplier = from property.terrain analysis (1.0 easy, 1.25 moderate, 1.5 difficult)
├── SUBTOTAL = (dig_cost + haul_off_cost) × access_multiplier
└── grade_work = IF terrain.grade_change_ft > 2 THEN additional_grading_cost ELSE 0

MODULE 2: STRUCTURAL (Shell)
├── shell_sqft = pool.dimensions.surface_area_sqft + (perimeter × average_depth × 2)
├── gunite_cost = shell_sqft × material_pricing['gunite'].installed_cost_per_sqft
├── rebar_cost = shell_sqft × material_pricing['rebar'].cost_per_sqft (includes #3 and #4 bars)
├── bond_beam_lnft = pool.dimensions.perimeter_ft
├── bond_beam_cost = bond_beam_lnft × material_pricing['bond_beam'].cost_per_lnft
├── footer_cost = IF deep_end > 8ft THEN enhanced_footer_cost ELSE standard_footer
├── steps_cost = step_count × cost_per_step
├── bench_cost = bench_lnft × cost_per_lnft
├── SUBTOTAL = sum of above
└── raised_wall_cost = IF pool.structural.raised_walls.enabled THEN height × length × rate

MODULE 3: PLUMBING
├── return_lines = CEIL(pool.dimensions.volume_gallons / 10000) × 2
├── suction_lines = 2 (main drain + skimmer minimum)
├── skimmers = CEIL(pool.dimensions.surface_area_sqft / 400)
├── spa_lines = IF spa.enabled THEN 4 (supply + return + jets + air)
├── water_feature_lines = count of enabled water features
├── total_pipe_ft = estimated from equipment pad distance + pool perimeter
├── pipe_cost = total_pipe_ft × material_pricing['plumbing_pipe'].cost_per_ft
├── fittings_cost = estimated_fitting_count × material_pricing['plumbing_fitting'].cost_each
├── labor = total_pipe_ft × labor_rate_per_ft
└── SUBTOTAL = pipe_cost + fittings_cost + labor

MODULE 4: ELECTRICAL
├── circuits_required = count of: pump + heater + lights + automation + spa_blower + 
│   booster_pump + water_features + landscape_lights + outdoor_kitchen
├── conduit_ft = estimated run lengths from equipment pad to each device
├── wire_cost = conduit_ft × material_pricing['electrical_wire'].cost_per_ft
├── gfci_breakers = circuits_required (each requires GFCI per code)
├── bonding_grid = pool.dimensions.perimeter_ft × bonding_rate
├── sub_panel = IF circuits > 6 THEN sub_panel_cost ELSE 0
├── labor = electrician_rate × estimated_hours
└── SUBTOTAL = wire_cost + gfci_cost + bonding_cost + sub_panel + labor

MODULE 5: INTERIOR FINISH
├── finish_sqft = shell surface area (floor + walls)
├── finish_cost = finish_sqft × material_pricing[interior_finish.type].installed_cost_per_sqft
├── waterline_tile = IF enabled THEN perimeter × tile_rows × tile_height × installed_rate
├── full_tile = IF enabled THEN finish_sqft × full_tile_installed_rate
├── mosaic = IF enabled THEN mosaic_area × mosaic_installed_rate
└── SUBTOTAL = finish_cost + waterline_tile + full_tile + mosaic

MODULE 6: EQUIPMENT
├── FOR EACH selected equipment item:
│   ├── unit_cost = equipment_catalog[id].dealer_cost × markup_pct
│   ├── install_cost = equipment_catalog[id].install_labor_cost
│   └── item_total = unit_cost + install_cost
├── equipment_pad_cost = pad_sqft × concrete_rate
├── equipment_pad_cover = IF required THEN cover_cost
└── SUBTOTAL = SUM(all item_totals) + pad_cost

MODULE 7: DECK & HARDSCAPE
├── deck_cost = deck.area_sqft × material_pricing[deck.material.type].installed_cost_per_sqft
├── coping_cost = pool.dimensions.perimeter_ft × material_pricing[deck.coping.type].installed_cost_per_lnft
├── expansion_joints = perimeter_ft × joint_rate
├── outdoor_kitchen = IF enabled THEN base_cost + SUM(appliance_costs) + countertop + base
├── pergola = IF enabled THEN cost_formulas['pergola'].base + sqft × per_sqft_rate
├── fire_feature = IF enabled THEN cost_formulas['fire_pit|fireplace'].base_cost
├── seating_walls = SUM(wall.length_ft × cost_per_lnft)
├── landscape = SUM(zone.area_sqft × rate_per_type)
└── SUBTOTAL = sum of above

MODULE 8: WATER FEATURES (each is a cost_formula lookup)
├── infinity_edge = IF enabled THEN FOR EACH wall:
│   cost_formulas['infinity_edge'].base + (length_ft × per_lnft) × multipliers
├── waterfalls = FOR EACH waterfall:
│   cost_formulas[waterfall.type].base × height_multiplier × width_multiplier
├── grotto = IF enabled THEN cost_formulas['grotto'].base + feature_costs
├── deck_jets = quantity × cost_formulas['deck_jets'].per_unit
├── bubblers = quantity × cost_formulas['bubblers'].per_unit
├── fire_water_bowls = quantity × cost_formulas['fire_water_bowl'].per_unit
├── spray_features = SUM(quantity × per_unit_rate)
├── sun_shelf = area_sqft × cost_formulas['sun_shelf'].per_sqft + bubblers + furniture
└── SUBTOTAL = sum of above

MODULE 9: PERMITS & ENGINEERING
├── permit_fee = region_based_lookup (database)
├── engineering_stamp = IF required THEN PE_fee
├── survey_fee = IF needed THEN survey_cost
├── soil_test = IF unknown_soil THEN geotech_cost
└── SUBTOTAL = sum of above

MODULE 10: OVERHEAD & MARGIN
├── subtotal = SUM(modules 1-9)
├── project_management = subtotal × PM_overhead_pct (8-12%)
├── insurance_bonding = subtotal × insurance_pct (2-4%)
├── contingency = subtotal × contingency_pct (5-8%)
├── pre_margin_total = subtotal + PM + insurance + contingency
├── margin = pre_margin_total × division_margin_pct
│   ├── Signature: 20-28%
│   ├── Ultra Luxury: 30-40%
│   └── Hospitality: 25-35%
├── TOTAL = pre_margin_total + margin
├── estimate_low = TOTAL × 0.90
└── estimate_high = TOTAL × 1.10

OUTPUT:
├── Itemized breakdown (all modules visible)
├── Summary by category
├── Total estimate range
├── Per-division margin applied
└── Comparison to division budget bands
    ├── Signature: Flag if outside $100K-$200K
    ├── Ultra Luxury: Flag if outside $250K-$2M+
    └── Hospitality: Custom scoping
```

---

## AGENTIC LEAD GENERATION — COMPLETE SYSTEM

### Agent Definitions

```typescript
// /agents/permit-scanner.ts
// Runs: Every 6 hours via EventBridge cron
// 
// 1. Query Shovels.ai: GET /permits?geo_id={service_area_zips}&type=new_residential&status=issued&after={last_scan}
// 2. Query ATTOM: Enrich each permit with property data (lot size, value, ownership)
// 3. Filter: lot_size > 0.25 acres AND (assessed_value > $300K OR neighborhood_median > $300K)
// 4. For new construction: extract builder info, check if builder is in our CRM
// 5. Write qualified permits to permit_leads table
// 6. Trigger lead_scoring agent for new entries

// /agents/property-intelligence.ts
// Runs: Every 12 hours via EventBridge cron
// 
// 1. Query ATTOM: properties in service area zips WHERE lot_sqft > 10000 AND has_pool = false
// 2. Query Regrid: get parcel boundaries for each
// 3. Cross-reference against existing contacts (don't re-scan known properties)
// 4. Score each: property_value × lot_size_factor × neighborhood_income_factor × proximity_factor
// 5. Top 50 scored properties → trigger satellite_analysis agent

// /agents/satellite-analysis.ts  
// Runs: On-demand, triggered by property-intelligence agent
// 
// 1. Fetch EagleView aerial image for property (or Google Maps Static fallback)
// 2. Send image to Claude Vision API with prompt:
//    "Analyze this aerial/satellite image of a residential property. Identify:
//     1. Does the property have a swimming pool? If yes, estimate dimensions and condition.
//     2. Estimate the usable backyard area in sqft.
//     3. Note any existing outdoor features (patio, deck, gazebo, etc.)
//     4. Rate pool construction feasibility (1-10) based on yard size, access, and layout.
//     5. If pool exists, rate renovation candidacy (1-10) based on visible condition.
//     Return structured JSON."
// 3. Parse Claude's response → update permit_leads with analysis
// 4. If feasibility_score > 7 OR renovation_score > 7 → trigger proposal_generation agent

// /agents/lead-scoring.ts
// Runs: On new permit_lead creation
//
// Score = weighted sum of:
//   property_value_score (0-25):    >$500K=25, >$400K=20, >$300K=15, >$200K=10
//   lot_size_score (0-20):          >1 acre=20, >0.5=15, >0.33=10, >0.25=5
//   no_pool_bonus (0 or 15):        no existing pool = 15
//   pool_condition_score (0-15):     poor/abandoned=15, fair=10, good=5 (renovation leads)
//   neighborhood_score (0-15):       based on median income / home values in zip
//   proximity_score (0-10):          <10mi=10, <25mi=7, <50mi=4, >50mi=1
//   new_construction_bonus (0-10):   new build permit = 10
//   recency_bonus (0-5):            permit < 30 days = 5, < 90 days = 3
//
// Total: 0-100
// Qualified threshold: 60+

// /agents/proposal-generation.ts
// Runs: For permit_leads with score >= 60 AND status = 'qualified'
//
// 1. Fetch property intelligence (aerial image, lot data, parcel boundary)
// 2. Infer ideal pool configuration based on:
//    - Lot size → pool size (max 60% of buildable zone)
//    - Property value → budget band → division assignment
//    - Neighborhood character (from satellite analysis)
//    - Standard popular configurations for the price range
// 3. Generate design configuration (auto-populated from inference)
// 4. Run cost estimation engine → get estimate range
// 5. Queue 2 render jobs: aerial view + eye-level view
// 6. When renders complete → assemble concept proposal PDF
// 7. Update permit_lead status → 'proposal_generated'
// 8. Trigger outreach_orchestrator

// /agents/outreach-orchestrator.ts
// Runs: For permit_leads with status = 'proposal_generated'
//
// Cadence:
//   Day 0: Email via AWS SES
//     - Subject: "We designed a pool concept for [address]"
//     - Body: 1 render image, estimate range, CTA to view interactive 3D
//     - Personalized per contact type (homeowner vs builder)
//
//   Day 3: SMS via Twilio (if phone available)
//     - "Hi [name], we created a custom pool design for your property at [address]. 
//        See it in 3D: [link]. Reply STOP to opt out."
//
//   Day 7: Direct mail via Lob API
//     - 6x9 postcard: front = photorealistic pool render on their property
//     - Back = estimate range, QR code to interactive viewer, phone number
//     - Lob prints, stamps, and mails. ~$0.92/piece.
//
//   Day 14: Follow-up email via AWS SES
//     - "Just checking in — your pool concept is still available to view"
//     - Include different render angle
//
//   Day 30: Final touch — different channel
//     - If email opened but no response → SMS
//     - If no engagement → mark as 'dormant', re-scan in 6 months
//
// Compliance:
//   - CAN-SPAM: unsubscribe link in every email, honor opt-outs within 10 days
//   - TCPA: only SMS if phone was from public record / opted in, honor STOP immediately
//   - Direct mail: no opt-in required (physical mail exempt from TCPA/CAN-SPAM)
//   - All outreach logged in activity_log
//   - Suppression list maintained (opt-outs, bounces, complaints)

// /agents/hospitality-prospector.ts
// Runs: Weekly via EventBridge cron
//
// 1. Scan hotel/resort construction pipeline sources:
//    - Shovels.ai: commercial permits in hospitality zones
//    - Construction Monitor: hotel-tagged permits
//    - Web scraping: Lodging Econometrics pipeline (new hotel announcements)
//    - Google Maps: search "hotels" + "resorts" in target markets
// 2. For existing properties:
//    - Fetch EagleView aerial imagery
//    - Claude Vision analysis: "Analyze this hotel/resort property. 
//      Identify pool areas, estimate condition, note renovation potential."
// 3. Research property management companies (web search)
// 4. Generate hospitality concept proposal (higher-end renders, different messaging)
// 5. Email to property management / ownership group
```

### Outreach Templates

```
EMAIL TEMPLATE: New Construction (to Builder)
─────────────────────────────────────────────
Subject: Pool partner for your [address] project?

Hi [builder_name],

We noticed you recently pulled a permit for new construction at 
[address]. Congratulations on the new project.

At Mizu by Orion, we specialize in designing and building pools 
that complement new homes — from the Gulf Coast's most popular 
family pools to ultra-luxury waterscapes.

We put together a concept design based on the lot:

[RENDER IMAGE - aerial view of property with concept pool]

Estimated range: $[low] - $[high]

Would you be open to a quick call about partnering on this project?

[CTA BUTTON: View Interactive 3D Design]

Best,
Mizu by Orion
[phone] | [website]

────────────────────────────────────────────

EMAIL TEMPLATE: Homeowner (no pool)
─────────────────────────────────────────────
Subject: What would a pool look like at [address]?

Hi [first_name],

We couldn't help but notice — your property at [address] has an 
incredible backyard that's practically made for a pool.

So we did something a little different: we designed one for you.

[RENDER IMAGE - photorealistic pool on their actual property]

This is a [pool_size] [pool_type] with [key_features], designed 
to fit your yard perfectly. Estimated investment: $[low] - $[high].

Want to see it from every angle? Click below to explore the full 
interactive 3D design:

[CTA BUTTON: Explore Your Pool in 3D →]

No commitment, no pressure — just a vision of what's possible.

Best,
Mizu by Orion
[phone] | [website]

────────────────────────────────────────────

DIRECT MAIL POSTCARD (Lob API)
─────────────────────────────────────────────
FRONT: Full-bleed photorealistic render of pool on their property
       Small Mizu logo in corner

BACK:
  "Your backyard. Your pool. Designed by AI, built by craftsmen."
  
  Estimated investment: $[low] - $[high]
  
  [QR CODE → interactive 3D viewer link]
  Scan to explore your pool design in 3D
  
  Mizu by Orion | [phone] | MizuByOrion.com
```

---

## PUBLIC API SPECIFICATION (For Licensees)

```
Base URL: https://api.mizupooldesigner.com/v1

Authentication: 
  Header: X-API-Key: mizu_live_xxxxxxxxxxxx

Rate Limiting:
  Basic tier:   100 designs/mo, 200 renders/mo
  Pro tier:     1,000 designs/mo, 2,000 renders/mo  
  Enterprise:   Unlimited (custom pricing)

────────────────────────────────────────────

POST /property/analyze
  Body: { "address": "1234 Main St, Gulfport, MS 39501" }
  Returns: { property_data, aerial_image_url, parcel_boundary, elevation, buildable_zone }

POST /property/analyze-survey
  Body: multipart/form-data with survey file (PDF, DWG, DXF, JPG, PNG)
  Returns: { extracted_data: { setbacks, dimensions, easements, utilities } }

POST /design/create
  Body: { property_id, configuration: DesignConfiguration }
  Returns: { design_id, preview_scene_url, estimate: { low, high, breakdown } }

PATCH /design/{id}
  Body: { configuration_patch: Partial<DesignConfiguration> }
  Returns: { design_id, preview_scene_url, estimate }

GET /design/{id}
  Returns: { design_id, configuration, status, estimate, preview_urls, cad_urls, render_urls }

GET /design/{id}/preview
  Returns: { scene_data } (Three.js scene graph JSON for embedding viewer)

POST /design/{id}/render
  Body: { type: "final", cameras: ["aerial", "eye_level", "twilight"], resolution: "4k" }
  Returns: { render_job_ids, estimated_completion_minutes }

GET /render/{job_id}
  Returns: { status, output_url, render_time_seconds }

GET /design/{id}/estimate
  Returns: { estimate_low, estimate_high, breakdown: { excavation, structural, ... } }

POST /design/{id}/cad
  Body: { formats: ["dxf", "pdf"], sheets: ["site_plan", "pool_plan", "sections", "plumbing", "electrical"] }
  Returns: { cad_job_id }

GET /cad/{job_id}
  Returns: { status, files: { site_plan_dxf: "url", pool_plan_pdf: "url", ... } }

POST /design/{id}/proposal
  Body: { contact_name, contact_email, include_sections: ["renders", "estimate", "timeline"] }
  Returns: { proposal_url, pdf_url, shareable_link }

GET /design/{id}/share
  Returns: { shareable_url } (public link to interactive 3D viewer + estimate)

────────────────────────────────────────────

Webhook Events (POST to licensee's webhook URL):
  design.created
  design.updated
  render.completed
  render.failed
  cad.completed
  estimate.calculated
```

---

## WIDGET EMBED SPECIFICATION

```html
<!-- OPTION 1: Web Component (recommended) -->
<script src="https://cdn.mizupooldesigner.com/widget/v1/mizu-designer.js"></script>
<mizu-pool-designer
  api-key="mizu_live_xxxxxxxxxxxx"
  division="signature"
  theme="light"
  primary-color="#1a3a5c"
  service-area="gulf-coast"
  show-estimate="true"
  show-3d-preview="true"
  lead-capture="true"
  on-lead-captured="handleMizuLead"
  on-design-complete="handleMizuDesign"
  width="100%"
  height="800px"
></mizu-pool-designer>

<script>
  function handleMizuLead(event) {
    // { name, email, phone, address, design_id, estimate_range }
    console.log('New lead:', event.detail);
  }
  function handleMizuDesign(event) {
    // { design_id, configuration, estimate, preview_url, shareable_link }
    console.log('Design complete:', event.detail);
  }
</script>

<!-- OPTION 2: iframe (simplest, zero integration) -->
<iframe 
  src="https://designer.mizubyorion.com/embed?key=mizu_live_xxx&division=signature&theme=light"
  width="100%" 
  height="800" 
  frameborder="0"
  allow="fullscreen"
></iframe>
```

---

## ADMIN CHAT — TOOL DEFINITIONS FOR CLAUDE

```typescript
// Tools available to Claude in the admin chat interface

const adminChatTools = [
  {
    name: "update_material_pricing",
    description: "Update material or labor pricing in the cost database",
    parameters: {
      item_name: "string",
      category: "string", 
      field: "material_cost_per_unit | labor_cost_per_unit",
      new_value: "number",
      region: "string (default: gulf_coast)"
    }
  },
  {
    name: "update_equipment_catalog", 
    description: "Add, update, or deactivate equipment in the catalog",
    parameters: {
      action: "add | update | deactivate",
      equipment_id: "string (for update/deactivate)",
      data: "Partial<EquipmentCatalog>"
    }
  },
  {
    name: "query_crm",
    description: "Search and filter CRM contacts and leads",
    parameters: {
      natural_language_query: "string",  // Claude converts to SQL
      limit: "number"
    }
  },
  {
    name: "generate_design",
    description: "Create a new pool design for a property",
    parameters: {
      address: "string",
      division: "signature | ultra_luxury | hospitality",
      configuration_hints: "Partial<DesignConfiguration>"  // partial config, AI fills the rest
    }
  },
  {
    name: "queue_render",
    description: "Submit a render job for a design",
    parameters: {
      design_id: "string",
      cameras: "string[]",
      priority: "number"
    }
  },
  {
    name: "generate_proposal",
    description: "Create and optionally send a proposal",
    parameters: {
      design_id: "string",
      contact_id: "string",
      send_immediately: "boolean",
      send_via: "email | sms | both"
    }
  },
  {
    name: "manage_agents",
    description: "Start, stop, or configure background agents",
    parameters: {
      agent: "permit_scanner | property_intelligence | satellite_analysis | outreach",
      action: "start | stop | configure | status",
      config: "Record<string, any>"
    }
  },
  {
    name: "scale_infrastructure",
    description: "Scale AWS resources up or down",
    parameters: {
      service: "render_gpu | api_server | worker",
      action: "scale_up | scale_down | set_count",
      count: "number",
      instance_type: "string (for GPU)"
    }
  },
  {
    name: "deploy",
    description: "Trigger CI/CD deployment",
    parameters: {
      environment: "dev | staging | production",
      service: "all | api | frontend | workers | agents",
      branch: "string (default: main)"
    }
  },
  {
    name: "query_analytics",
    description: "Get business analytics and metrics",
    parameters: {
      natural_language_query: "string",  // "leads this week", "conversion rate by division", etc.
      date_range: "string"
    }
  },
  {
    name: "run_sql",
    description: "Execute a read-only SQL query (for complex queries Claude generates)",
    parameters: {
      query: "string",  // SELECT only, Claude generates from natural language
      confirm: "boolean"
    }
  }
];
```

---

## MONTHLY OPERATING COSTS AT SCALE

| Category | Service | Monthly | Notes |
|----------|---------|---------|-------|
| **Compute** | ECS Fargate (API + Workers) | $400-1,200 | Auto-scales with load |
| | EC2 G5 GPU (Rendering) | $600-3,000 | Spot instances reduce ~60% |
| | Lambda (Event handlers) | $10-50 | Near-zero at low volume |
| **Data** | RDS PostgreSQL | $200-400 | db.r6g.large Multi-AZ |
| | ElastiCache Redis | $150-250 | cache.r6g.large |
| | OpenSearch | $75-150 | t3.small.search |
| **Storage** | Cloudflare R2 | $50-200 | Zero egress — huge savings |
| | S3 (backup) | $25-50 | |
| **External APIs** | EagleView | $500-3,000 | Volume-negotiated |
| | Google Maps Platform | $200-500 | Geocoding, elevation, satellite |
| | Shovels.ai | $500-1,500 | Permit data feed |
| | ATTOM Data | $300-1,000 | Property enrichment |
| | Regrid | $100-300 | Parcel boundaries |
| | Claude API (Anthropic) | $300-1,000 | Vision analysis, agent reasoning, chat |
| **Commerce** | Stripe | 2.9% + $0.30/txn | Pass-through |
| | DocuSign/DocuSeal | $0-200 | DocuSeal is free self-hosted |
| **Outreach** | AWS SES | $10-50 | $0.10/1000 emails |
| | Twilio SMS | $50-200 | $0.0079/SMS |
| | Lob Direct Mail | $200-1,000 | $0.65-1.20/piece |
| **Infrastructure** | Cloudflare Pro | $20 | CDN, WAF, DDoS |
| | AWS API Gateway | $10-50 | |
| | Secrets Manager | $10-20 | |
| | CloudWatch | $25-75 | Logs, metrics |
| | Sentry | $26 | Error tracking |
| **DevOps** | GitHub Team | $16 | 4 users |
| | Terraform Cloud | $0 | Free tier |
| **TOTAL** | | **$3,800 - $14,200/mo** | Scales with volume |

**Break-even**: At $150K average pool contract with 25% margin = $37,500 gross profit per pool. One additional pool closed per month due to this system pays for the entire platform at max scale. The lead gen alone should deliver far more than that.

---

## THE COMPLETE EXTERNAL API & SERVICE REGISTRY

Every external service, its purpose, its URL, and the account type needed:

| # | Service | URL | Purpose | Account Type | Free Tier? |
|---|---------|-----|---------|-------------|------------|
| 1 | **EagleView** | eagleview.com/developers | Aerial imagery + property measurements | Enterprise/Developer | 30-day trial |
| 2 | **Google Maps Platform** | console.cloud.google.com | Geocoding, satellite, elevation, Street View | Google Cloud | $200/mo credit |
| 3 | **Regrid** | regrid.com/api | Parcel boundaries, zoning, ownership | Developer | Limited free |
| 4 | **ATTOM Data** | api.gateway.attomdata.com | Property attributes, permits, valuations | Developer | Trial available |
| 5 | **Shovels.ai** | shovels.ai/api | Building permit data + contractor data | API subscription | Trial available |
| 6 | **Construction Monitor** | constructionmonitor.com | Real-time permit feeds | Enterprise | Demo available |
| 7 | **Mapbox** | mapbox.com | Satellite tiles, terrain, vector maps (fallback) | Developer | 50K loads free |
| 8 | **Anthropic Claude API** | api.anthropic.com | Vision analysis, agent reasoning, admin chat | API | Pay-per-use |
| 9 | **Stripe** | stripe.com | CC, ACH, invoicing, subscriptions, payment links | Business | Free (txn fees only) |
| 10 | **DocuSign** | developers.docusign.com | Digital signatures, embedded signing | Developer | Free sandbox |
| 11 | **DocuSeal** | docuseal.com | Open-source eSignature (self-hosted alternative) | Self-hosted | Free forever |
| 12 | **Twilio** | twilio.com | SMS outreach | Developer | Trial credit |
| 13 | **AWS SES** | aws.amazon.com/ses | Email delivery | AWS | 62K free/mo |
| 14 | **Lob** | lob.com | Programmatic direct mail (postcards, letters) | Developer | Free (pay per piece) |
| 15 | **GitHub** | github.com | Source control, CI/CD (Actions) | Team | Free for public |
| 16 | **Cloudflare** | cloudflare.com | CDN, R2 storage, Workers, DNS, DDoS | Pro | Generous free tier |
| 17 | **AWS** | aws.amazon.com | Compute, database, queues, secrets, monitoring | Business | Free tier 12mo |
| 18 | **Blender** | blender.org | Photorealistic 3D rendering (headless) | Open source | **Free forever** |
| 19 | **Three.js** | threejs.org | Browser 3D rendering | Open source | **Free forever** |
| 20 | **ezdxf** | pypi.org/project/ezdxf | DXF/DWG CAD file generation | Open source | **Free forever** |
| 21 | **CadQuery** | cadquery.readthedocs.io | Parametric 3D CAD geometry | Open source | **Free forever** |
| 22 | **LangGraph** | langchain-ai.github.io/langgraph | Agent orchestration framework | Open source | **Free forever** |

---

**This is the complete document. Every system, every schema, every API, every formula, every agent, every template, every cost, every endpoint. The machine is fully specified. Now it needs to be assembled.**
