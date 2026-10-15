"""Load the approved knowledge documents into the database.

    uv run python -m drivetruagent.ingest [path/to/folder]

Every file is checked before anything is written, and the load runs in one
transaction. Running it again only embeds what changed. Documents are indexed
with the local hashing embedder always, and with OpenAI's too when
OPENAI_API_KEY is set.
"""

import asyncio
import sys
from pathlib import Path

from drivetruagent.agent.factory import ingest_embedders
from drivetruagent.config import get_settings
from drivetruagent.db.session import create_engine, create_session_factory
from drivetruagent.knowledge.documents import SourceDocument, parse_document
from drivetruagent.knowledge.store import IngestReport, ingest


def load_documents(folder: Path) -> list[SourceDocument]:
    """Every `.md` file in the folder, parsed. Raises InvalidDocument."""
    paths = sorted(folder.glob("*.md"))
    if not paths:
        raise FileNotFoundError(f"No .md files in {folder}")
    return [
        parse_document(path.read_text(encoding="utf-8"), name=path.name)
        for path in paths
    ]


async def run(folder: Path) -> IngestReport:
    documents = load_documents(folder)
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            return await ingest(session, documents, ingest_embedders(settings))
    finally:
        await engine.dispose()


def main() -> None:
    folder = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path(get_settings().knowledge_dir)
    )
    report = asyncio.run(run(folder))
    print(f"Ingested knowledge from {folder}: {report}")


if __name__ == "__main__":
    main()
