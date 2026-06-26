import json
import models

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed(text):
    vec = _get_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def embed_and_store(article_id, text):
    vec = embed(text)
    models.save_embedding(article_id, json.dumps(vec))


def find_related(user_id, article_id, limit=5):
    target = models.get_embedding(article_id)
    if not target:
        return []
    rows = models.get_all_embeddings_for_user(user_id)
    scored = []
    for row in rows:
        if row["article_id"] == article_id:
            continue
        try:
            vec = json.loads(row["embedding_json"])
            scored.append((_cosine(target, vec), row))
        except Exception:
            pass
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def semantic_search(user_id, query, limit=10):
    """Return articles ranked by semantic similarity to query string."""
    try:
        q_vec = embed(query)
    except Exception:
        return []
    rows = models.get_all_embeddings_for_user(user_id)
    scored = []
    for row in rows:
        try:
            vec = json.loads(row["embedding_json"])
            scored.append((_cosine(q_vec, vec), row))
        except Exception:
            pass
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]
