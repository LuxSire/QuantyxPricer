"""RAG engine: indexes termsheet PDFs + in-memory assets/prices.

LLM provider is selected via AI_LLM_PROVIDER (default: anthropic):
  anthropic    — Claude via ANTHROPIC_API_KEY
  azure_openai — Azure OpenAI via AZURE_OPENAI_* env vars (data stays in your tenant)

Embeddings follow the same provider: HuggingFace for anthropic, Azure OpenAI for azure_openai.
"""

import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TERMSHEETS_DIR = PROJECT_ROOT / 'termsheets'
DOCS_DIR = PROJECT_ROOT / 'docs'

AI_LLM_PROVIDER              = os.getenv('AI_LLM_PROVIDER', 'anthropic').lower()
AI_ANTHROPIC_MODEL           = os.getenv('AI_ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001')
AZURE_OPENAI_ENDPOINT        = os.getenv('AZURE_OPENAI_ENDPOINT', '')
AZURE_OPENAI_API_KEY         = os.getenv('AZURE_OPENAI_API_KEY', '')
AZURE_OPENAI_API_VERSION     = os.getenv('AZURE_OPENAI_API_VERSION', '2024-02-01')
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv('AZURE_OPENAI_CHAT_DEPLOYMENT', 'gpt-4o-mini')
AZURE_OPENAI_EMB_DEPLOYMENT  = os.getenv('AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT', 'text-embedding-3-small')

# Module-level state
_index = None
_get_assets: Optional[Callable] = None
_get_prices: Optional[Callable] = None


def configure(get_assets_fn: Callable, get_prices_fn: Callable) -> None:
    """Wire in lambdas that return the current global assets/prices from main.py."""
    global _get_assets, _get_prices
    _get_assets = get_assets_fn
    _get_prices = get_prices_fn


def _load_documents():
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_core.documents import Document

    docs = []

    # Termsheet PDFs — one Document per page
    for pdf_path in sorted(TERMSHEETS_DIR.glob('*.pdf')):
        try:
            pages = PyPDFLoader(str(pdf_path)).load()
            for page in pages:
                page.metadata.update({'source_type': 'termsheet', 'filename': pdf_path.name})
            docs.extend(pages)
            logging.info(f'[AI] Loaded {len(pages)} pages from {pdf_path.name}')
        except Exception as e:
            logging.warning(f'[AI] Could not load {pdf_path.name}: {e}')

    # Application/product docs (QP_Technicals.pdf, QP_overview.pdf, etc.)
    for pdf_path in sorted(DOCS_DIR.glob('*.pdf')):
        try:
            pages = PyPDFLoader(str(pdf_path)).load()
            for page in pages:
                page.metadata.update({'source_type': 'docs', 'filename': pdf_path.name})
            docs.extend(pages)
            logging.info(f'[AI] Loaded {len(pages)} pages from docs/{pdf_path.name}')
        except Exception as e:
            logging.warning(f'[AI] Could not load docs/{pdf_path.name}: {e}')

    # Assets — one Document per instrument
    if _get_assets:
        for iid, asset in (_get_assets() or {}).items():
            try:
                d = asset.to_dict() if hasattr(asset, 'to_dict') else dict(asset)
                text = f"Asset {iid}:\n{json.dumps(d, indent=2, default=str)}"
                docs.append(Document(
                    page_content=text,
                    metadata={'source_type': 'asset', 'instrument_id': iid},
                ))
            except Exception as e:
                logging.warning(f'[AI] Could not serialize asset {iid}: {e}')

    # Prices — one Document per instrument (cashflow arrays excluded to save tokens)
    if _get_prices:
        for iid, price in (_get_prices() or {}).items():
            try:
                d = price.to_dict() if hasattr(price, 'to_dict') else dict(price)
                result = d.get('result') or {}
                slim = {k: v for k, v in result.items() if k not in ('cashflows', 'float_cashflows', 'fixed_cashflows')}
                text = f"Pricing result for {iid} (model={d.get('model')}):\n{json.dumps(slim, indent=2, default=str)}"
                docs.append(Document(
                    page_content=text,
                    metadata={'source_type': 'price', 'instrument_id': iid},
                ))
            except Exception as e:
                logging.warning(f'[AI] Could not serialize price {iid}: {e}')

    return docs


def _get_embeddings():
    if AI_LLM_PROVIDER == 'azure_openai':
        from langchain_openai import AzureOpenAIEmbeddings
        return AzureOpenAIEmbeddings(
            azure_deployment=AZURE_OPENAI_EMB_DEPLOYMENT,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
        )
    from langchain_community.embeddings import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')


def _get_llm():
    if AI_LLM_PROVIDER == 'azure_openai':
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_deployment=AZURE_OPENAI_CHAT_DEPLOYMENT,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            max_tokens=2048,
            temperature=0,
        )
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=AI_ANTHROPIC_MODEL, max_tokens=2048)


def build_index() -> int:
    """Build (or rebuild) the FAISS index from all sources. Returns chunk count."""
    global _index
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS

    docs = _load_documents()
    if not docs:
        raise ValueError('No documents found to index (no termsheets and no assets/prices).')

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_documents(docs)

    _index = FAISS.from_documents(chunks, _get_embeddings())

    logging.info(f'[AI] Index built: {len(chunks)} chunks from {len(docs)} documents')
    return len(chunks)


def query(question: str, k: int = 5) -> dict:
    """Retrieve relevant chunks and generate an answer via the configured LLM."""
    if _index is None:
        raise RuntimeError('Index not built. Call POST /ai/index first.')

    from langchain_core.messages import HumanMessage, SystemMessage

    retriever = _index.as_retriever(search_kwargs={'k': k})
    docs = retriever.invoke(question)

    context = '\n\n---\n\n'.join(d.page_content for d in docs)

    llm = _get_llm()
    messages = [
        SystemMessage(content=(
            'You are Lux, a financial analyst assistant for Quantyx Pricer. '
            'Answer questions using only the provided context from termsheets, '
            'asset definitions, and pricing results. '
            'Be concise, precise, and cite the source instrument when relevant.'
        )),
        HumanMessage(content=f'Context:\n{context}\n\nQuestion: {question}'),
    ]
    response = llm.invoke(messages)

    sources = [
        {
            'source_type': d.metadata.get('source_type'),
            'instrument_id': d.metadata.get('instrument_id'),
            'filename': d.metadata.get('filename'),
            'page': d.metadata.get('page'),
            'excerpt': d.page_content[:300],
        }
        for d in docs
    ]

    return {'answer': response.content, 'sources': sources}


def index_ready() -> bool:
    return _index is not None
