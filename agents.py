import os

from crewai import LLM, Agent
from dotenv import load_dotenv

from tools import pdf_tool, search_tool

load_dotenv()


# groq -> cloudflare(fallback)
def _get_llm():
    override = os.getenv("LLM_MODEL")
    if override:
        return LLM(model=override, temperature=0.3)

    if os.getenv("GROQ_API_KEY"):
        return LLM(model="groq/llama-3.3-70b-versatile", temperature=0.3)

    cf_token = os.getenv("CLOUDFLARE_AI_API_TOKEN")
    cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if cf_token and cf_account:
        return LLM(
            model="cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            api_key=cf_token,
            temperature=0.3,
        )

    raise ValueError(
        "set GROQ_API_KEY, CLOUDFLARE_AI_API_TOKEN, or GEMINI_API_KEY in .env"
    )


llm = _get_llm()

verifier = Agent(
    role="Financial Document Verifier",
    goal="Verify the uploaded file is a real financial document and flag any quality issues.",
    verbose=True,
    memory=True,
    backstory=(
        "Compliance officer with years of experience validating SEC filings, "
        "earnings reports, and balance sheets. You check for real financial data "
        "and flag anything suspicious or missing."
    ),
    tools=[pdf_tool],
    llm=llm,
    max_iter=5,
    max_rpm=10,
    allow_delegation=False,
)

financial_analyst = Agent(
    role="Senior Financial Analyst",
    goal="Analyze the financial document to answer: {query}",
    verbose=True,
    memory=True,
    backstory=(
        "12 years in equity research and corporate finance. You read quarterly "
        "reports, 10-Ks, and balance sheets daily. You pull concrete numbers — "
        "revenue, margins, EPS, free cash flow — and never fabricate figures."
    ),
    tools=[pdf_tool, search_tool],
    llm=llm,
    max_iter=5,
    max_rpm=10,
    allow_delegation=False,
)

# downstream agents work from context passed by previous tasks,
# they don't need pdf or search tools
investment_advisor = Agent(
    role="Investment Advisor",
    goal="Provide data-backed investment recommendations from the analyzed document.",
    verbose=True,
    memory=True,
    backstory=(
        "CFA charterholder with 10 years managing portfolios. You translate "
        "financial metrics into buy/hold/sell calls with clear reasoning, always "
        "disclose risks, and never guarantee returns."
    ),
    tools=[],
    llm=llm,
    max_iter=5,
    max_rpm=10,
    allow_delegation=False,
)

risk_assessor = Agent(
    role="Risk Assessment Specialist",
    goal="Identify and quantify financial risks including market, credit, and operational exposure.",
    verbose=True,
    memory=True,
    backstory=(
        "Risk management professional from major financial institutions. You "
        "stress-test assumptions, evaluate downside scenarios, and provide risk "
        "ratings tied to actual data from the document."
    ),
    tools=[],
    llm=llm,
    max_iter=5,
    max_rpm=10,
    allow_delegation=False,
)
