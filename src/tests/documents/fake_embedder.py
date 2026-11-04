"""Embedders for the document tests, named through AUTOMC_EMBEDDER: a bag of words hashed into 768 numbers (texts sharing words get
close vectors), no model and no Ollama."""
import hashlib
import math
import re

from tools.documents.index import DIMS, EmbedderUnavailable


class FakeEmbedder:
    ID = "fake:bag-of-words@1"

    def model_id(self):
        return self.ID

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * DIMS
            v[0] = 0.1                                      # never a zero vector
            for w in re.findall(r"[a-z0-9]{3,}", re.sub(r"^search_(document|query): ", "", t).lower()):
                v[1 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (DIMS - 1)] += 1.0
            n = math.sqrt(sum(x * x for x in v))
            out.append([x / n for x in v])
        return out


class OtherModel(FakeEmbedder):
    ID = "fake:bag-of-words@2"


class Unavailable:
    def model_id(self):
        raise EmbedderUnavailable("the test's embedding model does not answer")

    def embed(self, texts):
        raise EmbedderUnavailable("the test's embedding model does not answer")
