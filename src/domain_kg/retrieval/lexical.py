"""Exhaust bounded eligible fulltext keys; score only authorized corpus statistics."""

import math
import re
import unicodedata
from collections import Counter

from domain_kg.engine.scope import ScopePredicates
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.graph.repository import ACCEPTED
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.evidence import SealedEvidence
from domain_kg.retrieval.models import EvidenceCandidate, SearchRequest
from domain_kg.revisions.models import ReadScope
from domain_kg.storage.neo4j import inconsistent


def terms(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    result = re.findall(r"[^\W_]+(?:[.:/@_-][^\W_]+)*", text)
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(sequence)
        result.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return result


class LexicalRetrievalBranch:
    def __init__(self, queue: JobQueue) -> None:
        self.evidence = SealedEvidence(queue)
        self.resolver = self.evidence.resolver

    async def retrieve(self, scope: ReadScope, request: SearchRequest) -> list[EvidenceCandidate]:
        request = SearchRequest.model_validate(request)
        if scope.view.kg_id not in request.kg_ids or scope.filters != request.filters:
            raise ValueError("lexical request and immutable scope differ")
        data = await self.evidence.load(scope, "lexical")
        chunks = data.chunks
        if not chunks:
            return []
        query_terms = set(terms(request.query))
        if len(query_terms) > 512:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "The lexical query exceeds the term budget."
            )
        expected = {payload["projection_id"]: payload for payload in data.payloads}
        graph = await self.evidence.queue.router.for_graph(scope.access, scope.view.kg_id)
        identifiers = sorted(chunks)
        found = set()
        for start in range(0, len(identifiers), 256):
            batch = identifiers[start : start + 256]
            # UUID-derived hex keys are server data, never user Lucene syntax.
            lucene = (
                "lexical_key:(" + " OR ".join(expected[key]["lexical_key"] for key in batch) + ")"
            )
            try:
                records, _, _ = await graph.driver.execute_query(
                    "CALL db.index.fulltext.queryNodes('scoped_evidence_text',$lucene) "
                    "YIELD node AS n WHERE " + ACCEPTED + " AND n.projection_id IN $ids "
                    "RETURN properties(n) AS value",
                    **ScopePredicates(scope).graph_parameters,
                    lucene=lucene,
                    ids=batch,
                )
            except Exception as error:
                raise BranchUnavailable("Lexical recall is unavailable.") from error
            for record in records:
                payload = record["value"]
                identifier = payload.get("projection_id")
                if identifier not in chunks or payload != expected[identifier]:
                    raise inconsistent()
                found.add(identifier)
        if found != set(chunks):
            raise inconsistent()
        frequencies = {
            identifier: Counter(terms(chunks[identifier].text)) for identifier in identifiers
        }
        lengths = {identifier: sum(value.values()) for identifier, value in frequencies.items()}
        average = sum(lengths.values()) / len(lengths) or 1.0
        document_frequency = {
            term: sum(term in value for value in frequencies.values()) for term in query_terms
        }
        scores = {}
        for identifier, counts in frequencies.items():
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if frequency:
                    inverse = math.log(
                        1
                        + (len(chunks) - document_frequency[term] + 0.5)
                        / (document_frequency[term] + 0.5)
                    )
                    score += (
                        inverse
                        * frequency
                        * 2.2
                        / (frequency + 1.2 * (0.25 + 0.75 * lengths[identifier] / average))
                    )
            if score > 0:
                scores[identifier] = score
        ranked = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:50]
        result = [
            data.candidate(chunks[identifier], "lexical", scores[identifier], rank)
            for rank, identifier in enumerate(ranked, 1)
        ]
        await self.resolver.revalidate(scope)
        return result
