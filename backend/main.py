"""
backend/main.py — FastAPI backend for the Nikola AI assistant.

Endpoints
─────────
  POST  /upload          — ingest a document into ChromaDB
  POST  /chat            — send a message; get a streamed response
  GET   /rag/files       — list indexed files with chunk counts & timestamps
  POST  /rag/remove      — remove a specific file from the vector store
  POST  /rag/clear-all   — wipe all chunks, files, and in-memory history
"""

import hashlib
import json
import logging
import os
import re
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()

VAULT_DIR = Path(os.getenv("VAULT_DIR", str(Path.home() / "vault")))
VAULT_DIR.mkdir(parents=True, exist_ok=True)

CHROMA_DIR = VAULT_DIR / ".chroma"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.1:8b")
COLLECTION_NAME = "nikola_docs"

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".nikola" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_handler = RotatingFileHandler(
    LOG_DIR / "backend.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

log = logging.getLogger("backend")

# ──────────────────────────────────────────────────────────────────────────────
# ChromaDB client
# ──────────────────────────────────────────────────────────────────────────────

_chroma_client = chromadb.PersistentClient(
    path=str(CHROMA_DIR),
    settings=Settings(anonymized_telemetry=False),
)
_collection = _chroma_client.get_or_create_collection(
    COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)

# ──────────────────────────────────────────────────────────────────────────────
# In-memory chat history  {session_id: [{"role": ..., "content": ...}]}
# ──────────────────────────────────────────────────────────────────────────────

_history: dict[str, list[dict[str, str]]] = {}

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Nikola Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# Embedding helper (calls Ollama /api/embeddings)
# ──────────────────────────────────────────────────────────────────────────────


def _embed(text: str) -> list[float]:
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


# ──────────────────────────────────────────────────────────────────────────────
# Text chunking
# ──────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _chunk_text(text: str) -> list[str]:
    """Split *text* into overlapping chunks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _doc_id(filename: str, idx: int) -> str:
    return hashlib.sha256(f"{filename}:{idx}".encode()).hexdigest()[:20]


# ──────────────────────────────────────────────────────────────────────────────
# Filename validation
# ──────────────────────────────────────────────────────────────────────────────

# Only allow names that look like safe filenames: word chars, dots, and hyphens.
_SAFE_NAME_RE = re.compile(r"^[\w.\-]{1,255}$")


def _validate_original_name(filename: str) -> str:
    """
    Validate and return the *basename* of *filename*.
    Raises HTTPException 400 for any name that could cause path traversal.
    The returned value is used only as metadata (not for filesystem paths).
    """
    name = os.path.basename(os.path.normpath(filename))
    if not name or not _SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename!r}")
    return name


def _stored_path(content: bytes, original_name: str) -> Path:
    """
    Derive a safe, deterministic storage path from the *content* hash and
    the file extension extracted from the validated *original_name*.

    The returned path is always inside VAULT_DIR and contains no user-
    controlled path components, so it cannot cause path traversal.
    """
    content_hash = hashlib.sha256(content).hexdigest()  # 64 hex chars
    # Use only the suffix from the already-validated name; limit its length
    suffix = Path(original_name).suffix[:10]
    stored_name = f"{content_hash}{suffix}"
    # VAULT_DIR is a fully-controlled constant; stored_name is hash-derived
    return VAULT_DIR / stored_name


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Document upload
# ──────────────────────────────────────────────────────────────────────────────


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    """Ingest a text or PDF document into ChromaDB."""
    original_name = _validate_original_name(file.filename or "")
    content = await file.read()

    # Store the file under a content-hash derived name (no user-input in path)
    dest = _stored_path(content, original_name)
    log.info("Upload: %s → %s", original_name, dest.name)
    dest.write_bytes(content)

    # Extract text
    text = _extract_text(dest)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file.")

    chunks = _chunk_text(text)
    ids, embeddings, documents, metadatas = [], [], [], []
    ts = int(time.time())

    for i, chunk in enumerate(chunks):
        ids.append(_doc_id(original_name, i))
        embeddings.append(_embed(chunk))
        documents.append(chunk)
        metadatas.append(
            {
                "filename": original_name,
                "stored_name": dest.name,
                "chunk_index": i,
                "timestamp": ts,
            }
        )

    _collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    log.info("Indexed %d chunks for %s", len(chunks), original_name)
    return {"filename": original_name, "chunks": len(chunks)}


def _extract_text(path: Path) -> str:
    """Extract plain text from a file (txt / pdf supported)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfminer.high_level as pdf_hl

            return pdf_hl.extract_text(str(path))
        except ImportError:
            raise HTTPException(
                status_code=422,
                detail="pdfminer.six is required for PDF support.",
            )
    # Default: read as UTF-8 text
    return path.read_text(encoding="utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# Routes — RAG files list
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/rag/files")
def list_rag_files() -> dict[str, Any]:
    """Return a list of indexed files with chunk counts and timestamps."""
    results = _collection.get(include=["metadatas"])
    metadatas = results.get("metadatas") or []

    file_info: dict[str, dict] = {}
    for meta in metadatas:
        if meta is None:
            continue
        fname = meta.get("filename", "unknown")
        ts = meta.get("timestamp", 0)
        if fname not in file_info:
            file_info[fname] = {"filename": fname, "chunks": 0, "timestamp": ts}
        file_info[fname]["chunks"] += 1

    files = sorted(file_info.values(), key=lambda x: x["timestamp"], reverse=True)
    return {"files": files}


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Remove a file
# ──────────────────────────────────────────────────────────────────────────────


class RemoveRequest(BaseModel):
    filename: str


@app.post("/rag/remove")
def remove_file(req: RemoveRequest) -> dict[str, str]:
    """Delete a file's chunks from ChromaDB and remove the physical file."""
    original_name = _validate_original_name(req.filename)
    log.info("Removing file: %s", original_name)

    # Fetch metadata to discover the hash-derived stored filename
    results = _collection.get(
        where={"filename": original_name},
        include=["metadatas"],
    )
    ids_to_delete = results.get("ids") or []
    metadatas_found = results.get("metadatas") or []

    # Collect unique stored_name values (path is hash-derived, not from user)
    stored_names: set[str] = set()
    for meta in metadatas_found:
        if meta and meta.get("stored_name"):
            stored_names.add(meta["stored_name"])

    if ids_to_delete:
        _collection.delete(ids=ids_to_delete)
        log.info("Deleted %d chunks for %s", len(ids_to_delete), original_name)

    # Remove physical files — paths are derived from our hash, not user input
    for stored_name in stored_names:
        # stored_name is a hex digest + short suffix we wrote ourselves
        file_path = VAULT_DIR / stored_name
        if file_path.exists():
            file_path.unlink()
            log.info("Deleted stored file: %s", file_path)

    return {"status": "ok", "filename": original_name}


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Clear all
# ──────────────────────────────────────────────────────────────────────────────


@app.post("/rag/clear-all")
def clear_all() -> dict[str, str]:
    """
    Delete ALL chunks from ChromaDB, remove all physical files in the vault
    (but keep the vault folder), and clear in-memory chat history.
    """
    log.warning("clear-all triggered — wiping all RAG data")

    # 1. Drop and re-create the ChromaDB collection
    try:
        _chroma_client.delete_collection(COLLECTION_NAME)
    except Exception as exc:
        log.warning("Could not delete collection: %s", exc)

    global _collection
    _collection = _chroma_client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # 2. Wipe vault files (not the folder itself)
    wiped = 0
    for item in VAULT_DIR.iterdir():
        if item.is_file():
            try:
                item.unlink()
                wiped += 1
            except Exception as exc:
                log.warning("Could not delete %s: %s", item, exc)
        elif item.is_dir() and item.name != ".chroma":
            try:
                shutil.rmtree(item)
            except Exception as exc:
                log.warning("Could not remove dir %s: %s", item, exc)

    # 3. Clear in-memory history
    _history.clear()

    log.info("clear-all complete (wiped %d files)", wiped)
    return {"status": "ok", "wiped_files": str(wiped)}


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Chat
# ──────────────────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    top_k: int = 4


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """
    Retrieve relevant chunks, build a context-augmented prompt, and
    stream the Ollama response back to the client.
    """
    # Retrieve context from ChromaDB
    query_embedding = _embed(req.message)
    try:
        results = _collection.query(
            query_embeddings=[query_embedding],
            n_results=req.top_k,
            include=["documents"],
        )
        context_docs = results.get("documents", [[]])[0]
    except Exception:
        context_docs = []

    context = "\n\n".join(context_docs) if context_docs else ""

    # Build prompt
    system_prompt = (
        "You are Nikola, a helpful AI personal assistant. "
        "Use the following context if relevant:\n\n"
        f"{context}\n\n"
        "Answer the user's question concisely and accurately."
    )

    history = _history.setdefault(req.session_id, [])
    history.append({"role": "user", "content": req.message})

    messages = [{"role": "system", "content": system_prompt}] + history

    async def _stream():
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE_URL}/api/chat",
                json={"model": CHAT_MODEL, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                full_reply = []
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    token = data.get("message", {}).get("content", "")
                    if token:
                        full_reply.append(token)
                        yield token
                    if data.get("done"):
                        break
                # Append full assistant reply to history
                history.append(
                    {"role": "assistant", "content": "".join(full_reply)}
                )

    return StreamingResponse(_stream(), media_type="text/plain")
