"""
Blog-upload normalizer.

Turns a raw blog post the expert wrote into the canonical ``conversation``
shape that ``interview_calls`` stores for a voice interview — a dict of the form
``{"items": [{"role": "user", "content": [{"text": ...}]}]}``.

Design (see PLAN-blog-upload.md, D3): the post is wrapped as expert-attributed
``[USER]`` turn(s). We do NOT synthesize a fake interviewer Q&A — the blog text
is already the expert's own first-person writing, so the whole post becomes one
``user`` item (short posts) or a few paragraph-chunked ``user`` items (long
posts). Downstream (``TranscriptProcessor``) every ``[USER]`` turn is analyzed as
the expert's own voice, which is exactly what a blog post is.
"""

# Posts at or below this length become a single user turn. Longer posts are
# split into paragraph-grouped chunks so no single turn is unwieldy, while still
# keeping whole paragraphs together.
CHUNK_THRESHOLD_CHARS = 2000

# Target upper bound for each chunk when splitting a long post. Paragraphs are
# accumulated into a chunk until adding the next one would exceed this; a single
# paragraph longer than this is emitted as its own chunk rather than split
# mid-paragraph.
CHUNK_TARGET_CHARS = 2000


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs on blank lines."""
    paragraphs = []
    for block in text.split("\n\n"):
        stripped = block.strip()
        if stripped:
            paragraphs.append(stripped)
    return paragraphs


def _chunk_paragraphs(paragraphs: list[str]) -> list[str]:
    """Group consecutive paragraphs into chunks near CHUNK_TARGET_CHARS."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        # Start a new chunk if the current one is non-empty and adding this
        # paragraph would push it past the target size.
        if current and current_len + para_len > CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        current.append(para)
        # +2 accounts for the "\n\n" joiner between paragraphs.
        current_len += para_len + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def normalize_blog_post(text: str) -> dict:
    """
    Convert raw blog-post text into the canonical conversation dict.

    Args:
        text: The raw post text (already validated for length by the caller).

    Returns:
        dict: ``{"items": [{"role": "user", "content": [{"text": <chunk>}]}, ...]}``
              — one item for short posts, paragraph-chunked items for long ones.
    """
    cleaned = (text or "").strip()

    if not cleaned:
        return {"items": []}

    if len(cleaned) <= CHUNK_THRESHOLD_CHARS:
        chunks = [cleaned]
    else:
        paragraphs = _split_paragraphs(cleaned)
        # No blank-line structure to chunk on — keep the whole post as one turn.
        chunks = _chunk_paragraphs(paragraphs) if paragraphs else [cleaned]

    # Each item carries type="message" to match the canonical voice-call shape.
    # TranscriptProcessor._filter_conversation_items skips any item whose type is
    # not "message" when building the chat agent's knowledge base (the
    # interview_call_analysis.transcript column). Without this the blog turns are
    # filtered out and the avatar has no knowledge of the post.
    items = [
        {"type": "message", "role": "user", "content": [{"text": chunk}]}
        for chunk in chunks
    ]

    return {"items": items}
