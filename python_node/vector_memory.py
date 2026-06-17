import logging
import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger("quantelos.vector_memory")

class VectorMemory:
    def __init__(self, db_path="./data/chroma_db"):
        self.db_path = db_path
        try:
            self.client = chromadb.PersistentClient(path=self.db_path)
            # Use a lightweight local embedding model (all-MiniLM-L6-v2)
            self.emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
            self.collection = self.client.get_or_create_collection(
                name="trade_memory",
                embedding_function=self.emb_fn
            )
            logger.info("🧠 Vector RAG Memory (ChromaDB) Initialized.")
        except Exception as e:
            logger.error("Failed to initialize Vector Memory: %s", e)
            self.collection = None

    def store_lesson(self, trade_id: str, context: str, lesson: str, metadata: dict):
        """Store a failed trade lesson as a vector embedding."""
        if not self.collection:
            return
        
        # We embed the context (e.g. "RSI: 75, Bearish Trend, NFP News Spike") so we can search by context later.
        document_text = f"Context: {context} | Lesson: {lesson}"
        try:
            self.collection.add(
                documents=[document_text],
                metadatas=[metadata],
                ids=[trade_id]
            )
            logger.info("🧠 Lesson stored in Vector Memory [%s]", trade_id)
        except Exception as e:
            logger.error("Failed to store vector lesson: %s", e)

    def search_similar_failures(self, current_context: str, n_results: int = 3) -> list:
        """Search for historical failures that semantically match the current market context."""
        if not self.collection:
            return []
        
        try:
            # Query the vector DB
            results = self.collection.query(
                query_texts=[current_context],
                n_results=n_results
            )
            
            # Format results
            lessons = []
            if results and 'documents' in results and len(results['documents'][0]) > 0:
                docs = results['documents'][0]
                metas = results['metadatas'][0] if 'metadatas' in results else [{}] * len(docs)
                
                for doc, meta in zip(docs, metas):
                    lessons.append({
                        "semantic_lesson": doc,
                        "metadata": meta
                    })
            return lessons
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            return []
