from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from . import rag

router = APIRouter(prefix='/ai', tags=['AI'])


class QueryRequest(BaseModel):
    question: str
    k: int = 5


@router.post('/index', summary='Build RAG index from termsheets, assets and prices')
async def build_index():
    try:
        n_chunks = rag.build_index()
        return {'status': 'ok', 'chunks_indexed': n_chunks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/query', summary='Ask a question against the RAG index')
async def query(body: QueryRequest):
    try:
        return rag.query(body.question, k=body.k)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/status', summary='Check whether the RAG index is ready')
async def status():
    return {'index_ready': rag.index_ready()}
