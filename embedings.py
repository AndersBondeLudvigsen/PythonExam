from chromadb import PersistentClient
import numpy as np

client = PersistentClient(path="./chroma_db")
col = client.get_or_create_collection("transactions_collection")

# peek() returnerer et dict med lister
items = col.peek()

ids        = items["ids"]
docs       = items["documents"]
metas      = items["metadatas"]
embeddings = items["embeddings"]

# Print de første 10 (eller færre, hvis der ikke er så mange)
for i in range(min(10, len(ids))):
    print("ID:      ", ids[i])
    print("Doc:     ", docs[i])
    print("Meta:    ", metas[i])
    print("Embedding (first 5 dims):", np.round(embeddings[i][:5], 4).tolist())
    print("-" * 40)
