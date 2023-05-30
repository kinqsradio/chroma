import pytest
from typing import Generator, List, Callable, Iterator, cast
from chromadb.config import System, Settings
from chromadb.types import (
    SubmitEmbeddingRecord,
    VectorQuery,
    Operation,
    ScalarEncoding,
    Segment,
    SegmentScope,
    SeqId,
    Vector,
)
from chromadb.ingest import Producer
from chromadb.segment import VectorReader
import uuid
import time

from chromadb.segment.impl.vector.local_hnsw import LocalHnswSegment

from pytest import FixtureRequest
from itertools import count


def sqlite() -> Generator[System, None, None]:
    """Fixture generator for sqlite DB"""
    settings = Settings(sqlite_database=":memory:", allow_reset=True)
    system = System(settings)
    system.start()
    yield system
    system.stop()


def system_fixtures() -> List[Callable[[], Generator[System, None, None]]]:
    return [sqlite]


@pytest.fixture(scope="module", params=system_fixtures())
def system(request: FixtureRequest) -> Generator[System, None, None]:
    yield next(request.param())


@pytest.fixture(scope="function")
def sample_embeddings() -> Iterator[SubmitEmbeddingRecord]:
    """Generate a sequence of embeddings with the property that for each embedding
    (other than the first and last), it's nearest neighbor is the previous in the
    sequence, and it's second nearest neighbor is the subsequent"""

    def create_record(i: int) -> SubmitEmbeddingRecord:
        vector = [i**1.1, i**1.1]
        record = SubmitEmbeddingRecord(
            id=f"embedding_{i}",
            embedding=vector,
            encoding=ScalarEncoding.FLOAT32,
            metadata=None,
            operation=Operation.ADD,
        )
        return record

    return (create_record(i) for i in count())


segment_definition = Segment(
    id=uuid.uuid4(),
    type="test_type",
    scope=SegmentScope.VECTOR,
    topic="persistent://test/test/test_topic_1",
    collection=None,
    metadata=None,
)


def sync(segment: VectorReader, seq_id: SeqId) -> None:
    # Try for up to 5 seconds, then throw a TimeoutError
    start = time.time()
    while time.time() - start < 5:
        if segment.max_seqid() >= seq_id:
            return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for seq_id {seq_id}")


def test_insert_and_count(
    system: System, sample_embeddings: Iterator[SubmitEmbeddingRecord]
) -> None:
    system.reset()
    producer = system.instance(Producer)

    topic = str(segment_definition["topic"])

    max_id = 0
    for i in range(3):
        max_id = producer.submit_embedding(topic, next(sample_embeddings))

    segment = LocalHnswSegment(system, segment_definition)
    segment.start()

    sync(segment, max_id)

    assert segment.count() == 3
    for i in range(3):
        max_id = producer.submit_embedding(topic, next(sample_embeddings))

    sync(segment, max_id)
    assert segment.count() == 6


def approx_equal(a: float, b: float, epsilon: float = 0.0001) -> bool:
    return abs(a - b) < epsilon


def approx_equal_vector(a: Vector, b: Vector, epsilon: float = 0.0001) -> bool:
    return all(approx_equal(x, y, epsilon) for x, y in zip(a, b))


def test_get_vectors(
    system: System, sample_embeddings: Iterator[SubmitEmbeddingRecord]
) -> None:
    system.reset()
    producer = system.instance(Producer)

    topic = str(segment_definition["topic"])

    segment = LocalHnswSegment(system, segment_definition)
    segment.start()

    embeddings = [next(sample_embeddings) for i in range(10)]

    seq_ids: List[SeqId] = []
    for e in embeddings:
        seq_ids.append(producer.submit_embedding(topic, e))

    sync(segment, seq_ids[-1])

    # Get all items
    vectors = segment.get_vectors()
    assert len(vectors) == len(embeddings)
    vectors = sorted(vectors, key=lambda v: v["id"])
    for actual, expected, seq_id in zip(vectors, embeddings, seq_ids):
        assert actual["id"] == expected["id"]
        assert approx_equal_vector(
            actual["embedding"], cast(Vector, expected["embedding"])
        )
        assert actual["seq_id"] == seq_id

    # Get selected IDs
    ids = [e["id"] for e in embeddings[5:]]
    vectors = segment.get_vectors(ids=ids)
    assert len(vectors) == 5
    vectors = sorted(vectors, key=lambda v: v["id"])
    for actual, expected, seq_id in zip(vectors, embeddings[5:], seq_ids[5:]):
        assert actual["id"] == expected["id"]
        assert approx_equal_vector(
            actual["embedding"], cast(Vector, expected["embedding"])
        )
        assert actual["seq_id"] == seq_id


def test_ann_query(
    system: System, sample_embeddings: Iterator[SubmitEmbeddingRecord]
) -> None:
    system.reset()
    producer = system.instance(Producer)

    topic = str(segment_definition["topic"])

    segment = LocalHnswSegment(system, segment_definition)
    segment.start()

    embeddings = [next(sample_embeddings) for i in range(100)]

    seq_ids: List[SeqId] = []
    for e in embeddings:
        seq_ids.append(producer.submit_embedding(topic, e))

    sync(segment, seq_ids[-1])

    # Each item is its own nearest neighbor (one at a time)
    for e in embeddings:
        vector = cast(Vector, e["embedding"])
        query = VectorQuery(vectors=[vector], k=1, allowed_ids=None, options=None)
        results = segment.query_vectors(query)
        assert len(results) == 1
        assert len(results[0]) == 1
        assert results[0][0]["id"] == e["id"]

    # Each item is its own nearest neighbor (all at once)
    vectors = [cast(Vector, e["embedding"]) for e in embeddings]
    query = VectorQuery(vectors=vectors, k=1, allowed_ids=None, options=None)
    results = segment.query_vectors(query)
    assert len(results) == len(embeddings)
    for r, e in zip(results, embeddings):
        assert len(r) == 1
        assert r[0]["id"] == e["id"]

    # Each item's 3 nearest neighbors are itself and the item before and after
    test_embeddings = embeddings[1:-1]
    vectors = [cast(Vector, e["embedding"]) for e in test_embeddings]
    query = VectorQuery(vectors=vectors, k=3, allowed_ids=None, options=None)
    results = segment.query_vectors(query)
    assert len(results) == len(test_embeddings)

    for r, e, i in zip(results, test_embeddings, range(1, len(test_embeddings))):
        assert len(r) == 3
        assert r[0]["id"] == embeddings[i]["id"]
        assert r[1]["id"] == embeddings[i - 1]["id"]
        assert r[2]["id"] == embeddings[i + 1]["id"]