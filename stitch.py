"""Reconcile atomic ASR words from overlapping transcription windows.

Only identical normalized words with overlapping absolute timestamps are aligned.
Different recognition hypotheses cannot be reconciled losslessly: unmatched words
still belong to their window's non-overlapping core. Input dictionaries are never
modified, and the chosen word retains its timestamps and all caller metadata.
"""
from __future__ import annotations

import heapq
import math
import unicodedata


MAX_MIDPOINT_DRIFT = 0.75
MIN_OVERLAP_RATIO = 0.1
MAX_POINT_DRIFT = 0.03


def _normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", str(text)).casefold() if c.isalnum())


def _number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Word and window timestamps must be finite")
    return value


def _same_occurrence(a, b):
    """Nearby identical utterances are not necessarily the same spoken word."""
    if not a["normalized"] or a["normalized"] != b["normalized"]:
        return False
    drift = abs(a["mid"] - b["mid"])
    if drift > MAX_MIDPOINT_DRIFT:
        return False
    duration = min(a["end"] - a["start"], b["end"] - b["start"])
    if duration == 0:
        # ASR sometimes emits a zero-duration punctuation/word point. Only a
        # nearly identical timestamp provides evidence for reconciling it.
        return drift <= MAX_POINT_DRIFT + 1e-9
    overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return overlap > 0 and overlap >= duration * MIN_OVERLAP_RATIO - 1e-9


def _align(left, right, nodes):
    """Ordered matching: maximize count, then minimize total timestamp drift."""
    # The overlap contains seconds of speech, not the entire five-minute chunk.
    rows, cols = len(left), len(right)
    scores = [[(0, 0.0)] * (cols + 1) for _ in range(rows + 1)]
    moves = [bytearray(cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        a = nodes[left[i - 1]]
        for j in range(1, cols + 1):
            b = nodes[right[j - 1]]
            best, move = scores[i - 1][j], 1
            if scores[i][j - 1] > best:
                best, move = scores[i][j - 1], 2
            drift = abs(a["mid"] - b["mid"])
            if _same_occurrence(a, b):
                previous = scores[i - 1][j - 1]
                matched = (previous[0] + 1, previous[1] - drift)
                if matched >= best:
                    best, move = matched, 3
            scores[i][j], moves[i][j] = best, move
    pairs = []
    i, j = rows, cols
    while i and j:
        move = moves[i][j]
        if move == 3:
            pairs.append((left[i - 1], right[j - 1]))
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def reconcile_words(chunks):
    """Return words once, in alignment order, with absolute timestamps intact.

    Each chunk supplies ``core_start``, ``core_end``, ``window_start``,
    ``window_end`` and ``words``. Words supply finite ``start``, ``end`` and
    ``word`` fields; additional metadata, including ``_phrase``, is opaque.
    Cores should describe successive non-overlapping portions of the recording.
    Unmatched words use half-open core ownership, except the final recording
    endpoint. A matched pair survives even if timing drift put both outside their
    respective cores. Nonzero words require temporal overlap of at least 10% of
    the shorter word; zero-duration words require timestamps within 30 ms. Real
    repetitions within one window and disjoint utterances are not deduplicated.
    """
    chunks = sorted(chunks, key=lambda c: c["core_start"])
    if not chunks:
        return []
    nodes, per_chunk = [], []
    for ci, chunk in enumerate(chunks):
        core_start, core_end = _number(chunk["core_start"]), _number(chunk["core_end"])
        window_start, window_end = _number(chunk["window_start"]), _number(chunk["window_end"])
        if not window_start <= core_start <= core_end <= window_end:
            raise ValueError("A chunk core must be contained in its audio window")
        indices = []
        for wi, word in enumerate(chunk["words"]):
            start, end = _number(word["start"]), _number(word["end"])
            if end < start:
                raise ValueError("A word cannot end before its start")
            mid = (start + end) / 2
            owned = core_start <= mid < core_end or (ci == len(chunks) - 1 and mid == core_end)
            indices.append(len(nodes))
            nodes.append(dict(word=word, start=start, end=end, mid=mid, chunk=ci,
                              index=wi, owned=owned, normalized=_normalized(word["word"]),
                              context=min(mid - window_start, window_end - mid)))
        per_chunk.append(indices)

    parents = list(range(len(nodes)))
    members = [{n["chunk"]} for n in nodes]

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for ci in range(len(chunks) - 1):
        left, right = chunks[ci], chunks[ci + 1]
        overlap_start = max(_number(left["window_start"]), _number(right["window_start"]))
        overlap_end = min(_number(left["window_end"]), _number(right["window_end"]))
        if overlap_end <= overlap_start:
            continue
        candidates = []
        for indices in (per_chunk[ci], per_chunk[ci + 1]):
            candidates.append([i for i in indices if
                               overlap_start - MAX_MIDPOINT_DRIFT <= nodes[i]["mid"]
                               <= overlap_end + MAX_MIDPOINT_DRIFT])
        for a, b in _align(*candidates, nodes):
            ra, rb = root(a), root(b)
            # Never fold two genuine repetitions from the same source window.
            if ra != rb and not members[ra].intersection(members[rb]):
                parents[rb] = ra
                members[ra].update(members[rb])

    groups = {}
    for i in range(len(nodes)):
        groups.setdefault(root(i), []).append(i)
    chosen = {}
    for group, indices in groups.items():
        if len(indices) > 1 or any(nodes[i]["owned"] for i in indices):
            # Core ownership first; then prefer the better-context recognition.
            chosen[group] = max(indices, key=lambda i: (nodes[i]["owned"], nodes[i]["context"], -i))

    # Alignment establishes order even when selected timestamp estimates cross.
    successors, indegrees = {g: set() for g in chosen}, {g: 0 for g in chosen}
    for indices in per_chunk:
        previous = None
        for index in indices:
            group = root(index)
            if group not in chosen:
                continue
            if previous is not None and group != previous and group not in successors[previous]:
                successors[previous].add(group)
                indegrees[group] += 1
            previous = group

    def order_key(group):
        node = nodes[chosen[group]]
        return node["start"], node["chunk"], node["index"], group

    ready = [order_key(g) for g, count in indegrees.items() if not count]
    heapq.heapify(ready)
    ordered = []
    while ready:
        *_, group = heapq.heappop(ready)
        ordered.append(group)
        for following in successors[group]:
            indegrees[following] -= 1
            if not indegrees[following]:
                heapq.heappush(ready, order_key(following))
    # Contradictory hypotheses in exotic multi-window overlaps can form a cycle.
    # Keep every chosen word rather than silently discarding the unresolved tail.
    if len(ordered) < len(chosen):
        emitted = set(ordered)
        ordered.extend(sorted((g for g in chosen if g not in emitted), key=order_key))
    return [dict(nodes[chosen[g]]["word"]) for g in ordered]
