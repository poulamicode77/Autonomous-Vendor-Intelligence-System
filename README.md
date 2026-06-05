# Autonomous-Vendor-Intelligence-System

![arch_diag](https://github.com/poulamicode77/Autonomous-Vendor-Intelligence-System/blob/main/procureagent_architecture.svg)

Now here's the complete deep-dive — every concept, every layer, every decision explained so you can answer anything thrown at you.

---

# ProcureAgent — Complete Technical Deep Dive

---

## 1. The Problem Statement (Know This Cold)

Large enterprises receive hundreds of vendor applications, partnership requests, and RFP responses every month. A Procurement analyst evaluating a single vendor manually does this:

- Googles the vendor for news, financials, legal issues, leadership changes
- Digs through SharePoint for past contracts, previous scorecards, SLA records
- Opens the rubric spreadsheet and manually scores across 5–6 dimensions
- Cross-checks the vendor against internal blacklists, certification requirements, geographic restrictions
- Writes a 3-page evaluation report

This takes **3 to 5 business days per vendor**. ProcureAgent compresses this to **under 10 minutes** by parallelizing the work across 4 specialized AI agents coordinated by a central orchestrator.

---

## 2. Why Multi-Agent and Not Just One LLM Call?

This is the first question you'll get. Your answer:

A single LLM call cannot reliably do all of this at once because:

- **Context window limitations** — 2 million tokens of vendor documents don't fit in one call
- **Tool access separation** — the web search tool, the AI Search retrieval tool, the scoring rubric tool, and the policy index are conceptually separate concerns. Mixing them in one agent creates prompt confusion and hallucinations
- **Parallelism** — the Research Agent and the Scorer Agent can run simultaneously once initial data is gathered. A single sequential call can't parallelize
- **Accountability** — if something goes wrong, you know exactly which agent produced the bad output. With one monolithic LLM call, debugging is nearly impossible
- **Specialized system prompts** — the Compliance Agent needs a very strict, citation-heavy system prompt. The Report Writer needs a concise, executive-tone prompt. One agent can't hold both personalities reliably

---

## 3. The Orchestrator Agent — Deep Dive

**What it is:** The brain of the system. It receives the user's raw query ("evaluate vendor XYZ for our Q3 ERP procurement"), breaks it into subtasks, decides which agent to call, in what order, and assembles the final output.

**Technology:** Azure AI Foundry Agent Service + Semantic Kernel `AgentGroupChat`

**How Semantic Kernel's AgentGroupChat works:**
- You define multiple `ChatCompletionAgent` objects, each with its own system prompt and name
- You add them to an `AgentGroupChat` instance
- You define a `KernelFunctionTerminationStrategy` — a function that tells the group when the conversation is done (e.g. when the Report Writer has produced output)
- You define a `KernelFunctionSelectionStrategy` — a function that decides which agent speaks next
- The Orchestrator is itself an agent in this group, acting as the "manager" who delegates

**The planning loop:**
1. User query arrives via FastAPI endpoint
2. Orchestrator decomposes it: "I need vendor news → Research Agent. I need a score → Scorer Agent. I need compliance check → Compliance Agent. I need a report → Report Writer."
3. Orchestrator invokes Research Agent first (it needs data before scoring can happen)
4. Research Agent returns structured JSON
5. Orchestrator passes that JSON to Scorer Agent and Compliance Agent (these can run in parallel)
6. Orchestrator collects both outputs, checks for conflicts (e.g. Compliance flags a violation the Scorer missed)
7. If conflict found → loops back to Research Agent for more evidence (this is the agentic loop)
8. Once satisfied → invokes Report Writer with all gathered data
9. Report Writer returns final structured report
10. Orchestrator delivers to user

**Why this is "agentic" and not just a pipeline:**
The key word is *dynamic routing*. In a pipeline, step 3 always follows step 2. In an agentic system, the Orchestrator *reasons* about what to do next based on intermediate results. If the Compliance Agent returns "insufficient evidence to flag," the Orchestrator can decide to loop back for more research rather than proceeding. That conditional, state-dependent decision-making is agency.

---

## 4. The Research Agent — Deep Dive

**Job:** Find all relevant information about a vendor from two sources — the live web and internal documents.

**Tool 1 — Bing Search API / Azure AI Foundry Grounding:**
- Called via Azure OpenAI's tool-calling feature
- The agent has a tool definition that accepts a search query and returns web snippets
- Agent autonomously decides what queries to run: "vendor XYZ funding 2024", "vendor XYZ lawsuit OR legal", "vendor XYZ CEO news"
- Returns structured results with source URLs for citation grounding

**Tool 2 — Azure AI Search retrieval:**
- The internal document index sits in Azure AI Search
- Documents were originally ingested by Azure Data Factory from SharePoint, SQL databases, and REST APIs
- Azure Document Intelligence parsed PDFs (contracts, RFPs, SoWs) into text chunks
- Those chunks were embedded using `text-embedding-3-large` (Azure OpenAI) and stored as vectors in Azure AI Search alongside their BM25 keyword index

**Hybrid retrieval explained:**
- BM25 is traditional keyword search — good for exact matches like vendor names, contract numbers
- Vector search is semantic — good for meaning-based queries like "vendor delivery track record"
- Hybrid combines both scores using Reciprocal Rank Fusion (RRF) — the final ranked list is more robust than either alone
- The agent passes a query, gets back the top-K most relevant document chunks with their source metadata

**Output format (JSON):**
```json
{
  "vendor": "XYZ Corp",
  "web_signals": [
    {"finding": "Series C funding round closed Jan 2024", "source": "techcrunch.com", "sentiment": "positive"},
    {"finding": "Supply chain lawsuit filed Q2 2023", "source": "reuters.com", "sentiment": "negative"}
  ],
  "internal_documents": [
    {"excerpt": "...", "source": "Contract_2022_XYZ.pdf", "relevance_score": 0.91}
  ]
}
```

---

## 5. The Scorer Agent — Deep Dive

**Job:** Take the Research Agent's output + an internal evaluation rubric and produce a structured numerical score across 5 dimensions.

**How the rubric gets loaded:**
The scoring rubric is a JSON file stored in Azure Blob Storage. At agent startup, it's loaded into the agent's context window as part of the system prompt. It looks like:

```json
{
  "dimensions": [
    {"name": "financial_stability", "weight": 0.25, "criteria": "..."},
    {"name": "delivery_track_record", "weight": 0.20, "criteria": "..."},
    {"name": "compliance_history", "weight": 0.20, "criteria": "..."},
    {"name": "pricing_competitiveness", "weight": 0.20, "criteria": "..."},
    {"name": "strategic_fit", "weight": 0.15, "criteria": "..."}
  ]
}
```

**Structured output / JSON mode:**
The Azure OpenAI call uses `response_format: {"type": "json_object"}` — this forces the model to always return valid JSON. This is critical because downstream agents and the Report Writer need to parse this output programmatically. Without JSON mode, LLMs sometimes prefix their output with "Sure! Here is the score:" which breaks parsing.

**Scoring output:**
```json
{
  "vendor": "XYZ Corp",
  "scores": {
    "financial_stability": {"score": 7.2, "rationale": "Recent Series C indicates strong backing but lawsuit creates uncertainty"},
    "delivery_track_record": {"score": 8.5, "rationale": "3 of 4 internal contracts completed on time per historical records"}
  },
  "weighted_total": 7.4,
  "recommendation": "PROCEED_WITH_CAUTION"
}
```

---

## 6. The Compliance Agent — Deep Dive

**Job:** Check whether the vendor violates any internal procurement policies. Not just flag yes/no — but cite the exact policy rule that is violated.

**Why RAG specifically here:**
Procurement policy documents are long (50–100 pages), updated frequently, and contain nuanced language. You cannot put the entire policy in the system prompt — it would exceed context limits and dilute attention. RAG retrieves only the 3–5 most relevant policy clauses for the specific vendor being evaluated.

**The policy index:**
- Policy PDFs are ingested via Azure Document Intelligence → chunked → embedded → indexed in a separate Azure AI Search index (separate from the vendor document index)
- The Compliance Agent queries this index with something like: "vendor geographic restrictions Southeast Asia" or "minimum ISO certification requirements"

**Citation grounding:**
A key feature — every flag the Compliance Agent raises must include the exact policy passage it's based on. This is non-negotiable for an enterprise compliance workflow. The agent is prompted to always include `"policy_reference"` in its output with the source document and chunk.

**Output:**
```json
{
  "vendor": "XYZ Corp",
  "violations": [
    {
      "rule": "All vendors must hold ISO 27001 certification",
      "policy_reference": "Procurement Policy v4.2, Section 3.1, Page 12",
      "status": "FAIL",
      "evidence": "No ISO 27001 found in vendor documentation or web search"
    }
  ],
  "overall_status": "NON_COMPLIANT"
}
```

---

## 7. The Report Writer Agent — Deep Dive

**Job:** Aggregate all three agents' structured outputs into a human-readable evaluation report.

**Why a separate agent for this:**
Report generation is a *different cognitive task* than research or scoring. It requires a completely different system prompt — one focused on executive communication, conciseness, and narrative structure. Mixing this with research or scoring degrades both.

**What it does:**
1. Receives the combined JSON from the Orchestrator (Research + Scorer + Compliance outputs)
2. Generates an executive summary (3–4 sentences, decision-ready)
3. Generates detailed findings section with evidence
4. Formats citations from the Compliance Agent into readable callouts
5. Outputs structured JSON that maps to a report template

**Delivery mechanism:**
- FastAPI endpoint returns the JSON to the frontend
- Azure Logic Apps can trigger an email delivery or Teams notification with the report attached
- Azure Function App handles the PDF rendering via a Jinja2 template if a downloadable report is needed

---

## 8. Memory and State — Cosmos DB

**Why Cosmos DB specifically:**
- Multi-turn conversations require memory — if an analyst asks "now re-evaluate XYZ but with stricter financial criteria," the system needs to remember the previous evaluation context
- Cosmos DB stores conversation history as JSON documents, keyed by `session_id`
- Each agent call appends to the conversation history stored in Cosmos DB
- On the next turn, the Orchestrator loads the last N turns from Cosmos DB and includes them in the context

**Session management:**
- Each user session gets a UUID `session_id`
- The FastAPI backend manages session lifecycle
- Cosmos DB TTL (Time To Live) is set to 24 hours for conversation sessions — auto-cleanup

---

## 9. The Ingestion Pipeline — How Documents Get Into the System

This is the offline pipeline that runs before any agent query happens:

1. **Azure Data Factory** triggers on a schedule (daily) or event (new file in SharePoint)
2. It copies raw files (PDFs, Word docs, Excel) to **Azure Data Lake Gen2**
3. **Azure Document Intelligence** (Form Recognizer) processes PDFs — extracts text, preserves table structure, handles scanned documents via OCR
4. A Python script (running in **Azure Function App**) chunks the extracted text using recursive character splitting (chunk size 512 tokens, overlap 64 tokens)
5. Each chunk is embedded using **Azure OpenAI `text-embedding-3-large`** (1536 dimensions)
6. The chunk text + embedding + metadata (source file, page number, document type, vendor name) is indexed into **Azure AI Search**

**Why chunk size 512 with overlap 64:**
- 512 tokens is large enough to contain a complete contract clause but small enough for precise retrieval
- 64-token overlap ensures that important sentences spanning a chunk boundary aren't lost
- Smaller chunks (128 tokens) give more precise retrieval but lose context. Larger chunks (1024+) reduce precision.

---

## 10. Security Architecture — Every Component Explained

**Azure Active Directory + Managed Identity:**
- Every Azure service (Azure Function App, Container App, Data Factory) is assigned a **Managed Identity** — a service principal whose credentials are managed automatically by Azure
- No passwords or API keys are stored in code or environment variables for service-to-service calls
- The Managed Identity is granted specific RBAC roles: e.g. the Function App's identity gets "Azure AI Developer" role on the Azure OpenAI resource

**Azure Key Vault:**
- External API keys that can't use Managed Identity (e.g. Bing Search API key) are stored in Key Vault
- The application fetches them at runtime using the Managed Identity — never hardcoded
- SSL/TLS certificates for the FastAPI endpoint are stored and rotated in Key Vault

**RBAC on Azure AI Search:**
- Procurement analysts get "Search Index Data Reader" role — they can query but not modify the index
- The ingestion pipeline's Managed Identity gets "Search Index Data Contributor" — it can write to the index
- No one gets subscription-level access

**Private Endpoints:**
- Azure AI Search, Cosmos DB, and Azure OpenAI are configured with Private Endpoints
- This means they are not accessible from the public internet — only from within the Azure Virtual Network
- The FastAPI app (in Azure Container Apps) runs inside the same VNet

---

## 11. Monitoring — What You Actually Track

**Azure Monitor + Application Insights:**
- Every agent call emits custom telemetry: agent name, latency, token count, tool calls made
- Application Insights tracks end-to-end request traces — you can see the entire chain from user query to final report in one trace
- Alerts are configured: if p95 latency exceeds 60 seconds, or if error rate exceeds 5%, an alert fires to the on-call channel

**Key metrics you monitor:**
- **Token consumption per agent** — which agent is burning the most tokens? (usually Research)
- **RAG retrieval quality** — measured by tracking whether retrieved chunks were actually used in the final response (grounding rate)
- **Compliance flag rate** — what % of vendor evaluations trigger a compliance flag? A sudden spike might mean a policy was updated and the index needs refreshing
- **Agent loop count** — how many times did the Orchestrator loop back to an agent? High loop counts indicate the agents aren't converging efficiently

**Power BI dashboard:**
- Pulls from Log Analytics workspace via a scheduled query
- Shows: daily evaluations completed, average evaluation time, top compliance failure reasons, cost per evaluation (token spend)

---

## 12. CI/CD Pipeline — How Code Gets to Production

**GitHub Actions workflow:**
1. Developer pushes to feature branch
2. GitHub Actions runs: unit tests → integration tests against a dev Azure AI Foundry workspace → code quality checks
3. Pull request merged to main
4. GitHub Actions builds Docker image → pushes to Azure Container Registry
5. Azure DevOps release pipeline picks up the new image → deploys to staging Container Apps environment
6. Automated evaluation suite runs against staging (50 pre-built vendor evaluation test cases with known correct outputs)
7. If evaluation pass rate ≥ 85% → auto-promote to production
8. Blue/green deployment — new version spins up alongside old, traffic shifts 10% → 50% → 100% over 30 minutes
9. MLflow logs model version, evaluation scores, and deployment metadata for audit trail

---

## 13. Hardest Interview Questions — Pre-Built Answers

**"How do you prevent hallucination in the Compliance Agent?"**
> "Three mechanisms. First, RAG with citation grounding — the agent is instructed to only flag violations it can cite from the retrieved policy passages, never from general knowledge. Second, we use Azure OpenAI's `json_object` response format with a schema that requires a `policy_reference` field for every flag — if there's no reference, the output is invalid and the agent is re-prompted. Third, we run a post-processing validation step that checks whether the cited policy chunk actually exists in the index before the output is delivered to the user."

**"What happens if the Orchestrator loops forever?"**
> "The `AgentGroupChat` termination strategy includes a maximum iteration count — currently set to 8 turns. If the Orchestrator hasn't produced a final report within 8 agent turns, it exits the loop and generates a partial report with a flag indicating incomplete evaluation. This is logged as a specific error type in Application Insights so we can analyze patterns and improve the termination conditions."

**"Why Azure AI Search over Pinecone or a dedicated vector DB?"**
> "Three reasons specific to this enterprise context. First, hybrid search — Azure AI Search natively combines BM25 keyword and vector search in a single query, which gives better results than vector-only for structured procurement documents where exact vendor names and contract numbers matter. Second, security — it integrates natively with Azure AD and supports Private Endpoints, keeping all data within the enterprise network boundary. Third, operational simplicity — we're already on Azure, so one less external dependency to manage, monitor, and secure."

**"How do you handle a vendor document that's a scanned image PDF?"**
> "Azure Document Intelligence handles this via its OCR capability. It can detect whether a PDF is digitally generated or scanned, and applies OCR to scanned pages. It also preserves table structure — for financial statements in tabular format, it extracts row-column relationships that would be lost with simple text extraction. The output is markdown-formatted text that preserves the document's logical structure before chunking."

**"What's the difference between Semantic Kernel and LangChain?"**
> "Both are LLM orchestration frameworks. Semantic Kernel is Microsoft-native, has first-class Azure OpenAI integration, and is built with enterprise .NET and Python patterns in mind — concepts like Plugins, Planners, and Memories map well to enterprise software architecture. LangChain has a larger community ecosystem but can be more complex to configure securely at enterprise scale. For an Azure-native stack where we're already using Azure AI Foundry, Semantic Kernel avoids extra abstraction layers and has better support for Azure-specific features like Managed Identity integration."

---

That covers every layer from the user query down to the CI/CD pipeline. You should now be able to answer questions ranging from "what does the Orchestrator do?" all the way down to "why did you pick chunk size 512?" Ready to move to the Salesforce integration project or jump to the Experience section?
