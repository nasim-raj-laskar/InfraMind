"""
core/vectordb.py — ChromaDB persistent vector store + Bedrock embeddings.
"""
import json
import hashlib
import logging
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import EmbeddingFunction
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter

from core.bedrock_client import bedrock_runtime
from config.config import (
    CHROMA_PATH, CHROMA_COLLECTION, CHUNK_K, MAX_DISTANCE,
    EMBED_BATCH_SIZE, EMBED_MAX_INPUT, MODEL_EMBED_ID,
    RUNBOOK_DIR, RUNBOOK_GLOB, RUNBOOK_HEADERS, RETRIEVAL_SUFFIX
)

logger = logging.getLogger("inframind.vectordb")


class BedrockEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input: list[str]) -> list[list[float]]:
        embeddings = []
        for text in input:
            response = bedrock_runtime.invoke_model(
                modelId=MODEL_EMBED_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"inputText": text[:EMBED_MAX_INPUT]})
            )
            result = json.loads(response["body"].read())
            embeddings.append(result["embedding"])
        return embeddings


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def build_vector_db(force_rebuild: bool = False):
    """
    Loads runbook markdown files → splits by headers → embeds → stores in ChromaDB.
    Skips re-embedding chunks that already exist (by content hash).
    Set force_rebuild=True to wipe and re-embed everything.
    """
    client   = chromadb.PersistentClient(path=CHROMA_PATH)
    embed_fn = BedrockEmbeddingFunction()

    if force_rebuild:
        try:
            client.delete_collection(CHROMA_COLLECTION)
            logger.info("Deleted existing collection for rebuild")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"}
    )

    loader   = DirectoryLoader(RUNBOOK_DIR, glob=RUNBOOK_GLOB, loader_cls=TextLoader)
    docs     = loader.load()
    logger.info("Loaded %d runbook files", len(docs))

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=RUNBOOK_HEADERS
    )
    splits = []
    for doc in docs:
        splits.extend(splitter.split_text(doc.page_content))
    logger.info("Split into %d chunks", len(splits))

    existing_ids          = set(collection.get()["ids"])
    new_docs, new_ids, new_metas = [], [], []

    for chunk in splits:
        chunk_id = _content_hash(chunk.page_content)
        if chunk_id not in existing_ids:
            new_docs.append(chunk.page_content)
            new_ids.append(chunk_id)
            new_metas.append(chunk.metadata)

    if new_docs:
        for i in range(0, len(new_docs), EMBED_BATCH_SIZE):
            collection.upsert(
                ids=new_ids[i:i+EMBED_BATCH_SIZE],
                documents=new_docs[i:i+EMBED_BATCH_SIZE],
                metadatas=new_metas[i:i+EMBED_BATCH_SIZE],
            )
        logger.info("Embedded and stored %d new chunks", len(new_docs))
    else:
        logger.info("All chunks already in ChromaDB — skipping re-embedding")

    return collection


def get_context(query: str, collection, k: int = None, max_distance: float = None) -> str:
    """
    Retrieve top-k runbook chunks relevant to the query.
    Filters by distance threshold to remove low-relevance chunks.
    """
    k            = k or CHUNK_K
    max_distance = max_distance or MAX_DISTANCE

    results   = collection.query(query_texts=[query], n_results=k)
    docs      = results["documents"][0]
    distances = results["distances"][0]

    filtered = [
        doc for doc, dist in zip(docs, distances)
        if dist < max_distance
    ]

    if len(filtered) < 2:
        filtered = docs[:2]

    logger.debug(
        "Retrieved %d chunks, kept %d after distance filter (threshold=%.2f)",
        len(docs), len(filtered), max_distance
    )
    return "\n---\n".join(filtered)


def build_retrieval_query(severity: str, message: str) -> str:
    """Build a clean retrieval query from normalized log fields."""
    return f"{severity} {message} {RETRIEVAL_SUFFIX}"
