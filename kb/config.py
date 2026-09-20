"""Central settings for the knowledge base. Change the embedding model here."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = BASE_DIR / "kb" / "chroma_store"
COLLECTION_NAME = "maintenance_cases"

# Multilingual model (~100 languages, including Hindi and Telugu).
# The e5 family expects these prefixes. If you switch to a model that does
# not use prefixes (e.g. paraphrase-multilingual-MiniLM-L12-v2), set both to "".
EMBED_MODEL = "intfloat/multilingual-e5-small"
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

DEFAULT_TOP_K = 5