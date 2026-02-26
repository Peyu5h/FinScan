from crewai import Task

from agents import financial_analyst, investment_advisor, risk_assessor, verifier
from tools import pdf_tool

verify_document = Task(
    description=(
        "Read the PDF document at path: {file_path}\n"
        "Use the financial_document_reader tool with exactly that path.\n"
        "Determine if it's a legitimate financial document (earnings report, "
        "10-K, 10-Q, annual report, balance sheet, etc). If it's not, say so. "
        "If it is, note the document type, company name, reporting period, "
        "and key sections present."
    ),
    expected_output=(
        "Short verification report:\n"
        "- Document type\n"
        "- Company name and reporting period\n"
        "- Confirmation that financial data exists\n"
        "- Any missing sections or data quality issues"
    ),
    agent=verifier,
    tools=[pdf_tool],
    async_execution=False,
)

analyze_financial_document = Task(
    description=(
        "The document at {file_path} has been verified. Now perform a thorough "
        "financial analysis to answer the user query: {query}\n"
        "Use the financial_document_reader tool with path {file_path} to read "
        "the full text. Extract key metrics: revenue, net income, operating "
        "margins, EPS, free cash flow, debt levels. Compare QoQ and YoY where "
        "available. Cite specific numbers from the document."
    ),
    expected_output=(
        "Structured financial analysis:\n"
        "- Executive summary (3-5 sentences)\n"
        "- Key metrics with actual figures\n"
        "- Trend analysis (QoQ / YoY)\n"
        "- Notable items: guidance, one-time charges, segment breakdowns\n"
        "- Direct answer to the user query with supporting data"
    ),
    agent=financial_analyst,
    tools=[pdf_tool],
    context=[verify_document],
    async_execution=False,
)

# downstream tasks work from context only, no need to re-read pdf
investment_analysis = Task(
    description=(
        "Based on the financial analysis above, provide investment recommendations. "
        "Evaluate valuation, growth trajectory, competitive position, and "
        "current market conditions. User query for context: {query}\n"
        "Ground every recommendation in specific metrics from the analysis. "
        "Include a bull case and a bear case."
    ),
    expected_output=(
        "Investment recommendation:\n"
        "- Overall call: buy / hold / sell with confidence level\n"
        "- Bull case with supporting metrics\n"
        "- Bear case with supporting metrics\n"
        "- Key catalysts and events to watch\n"
        "- Position sizing considerations\n"
        "- Risk disclaimer"
    ),
    agent=investment_advisor,
    context=[analyze_financial_document],
    async_execution=False,
)

risk_assessment = Task(
    description=(
        "Based on the financial analysis above, evaluate risks. Assess market "
        "risk, credit risk, liquidity risk, and operational risk. Identify "
        "specific risk factors and quantify exposure where possible. "
        "User context: {query}"
    ),
    expected_output=(
        "Risk assessment:\n"
        "- Overall risk rating: low / moderate / high\n"
        "- Market risk factors with quantified exposure\n"
        "- Credit and liquidity evaluation\n"
        "- Operational risks specific to the company\n"
        "- Warning signs to monitor\n"
        "- Mitigation suggestions"
    ),
    agent=risk_assessor,
    context=[analyze_financial_document],
    async_execution=False,
)
