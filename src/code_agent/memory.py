from pathlib import Path

import yaml
from openai import AsyncOpenAI
from pydantic import BaseModel, TypeAdapter

from code_agent import settings


class ExtractedMemory(BaseModel):
    """A memory item returned by the language model."""

    name: str
    type: str
    description: str
    body: str


_EXTRACTED_MEMORIES_ADAPTER = TypeAdapter(list[ExtractedMemory])
_SELECTED_MEMORIES_ADAPTER = TypeAdapter(list[int])

'''
When a new agent loop comes, use select_relevant_memories to learn the what the memory needed by the context.
When agent shundown, call extract_memories.
TODO: When and how to load memory?

'''

class Memory :
    '''
    MEMORY.md is the basic memory catalog file.
    The line like:
    - [index]:[{memory_name}]({memory_file}) - {description}
    Example:
    - [0]:[user-preference-tabs](user-preference-tabs.md) — User prefers tabs for indentation

    
    memory file use md file to store memory content.Begin with YAML frontmatter to record meta data.

    Begin with YAML frontmatter. Record meta data
    ---
    name: user-preference-tabs
    description: User prefers tabs for indentation
    type: user
    ---

    User prefers using tabs, not spaces, for indentation.
    **Why:** Consistency with existing codebase conventions.
    **How to apply:** Always use tabs when writing or editing files.
    '''

    path : Path
    client : AsyncOpenAI
    def __init__(self,path : Path,client : AsyncOpenAI) :
        self.path = path
        self.client = client

    # extract memory from current messages.
    async def extract_memories(self,messages: list[dict[str,str]]) -> None:
        # extract dialogue from messages. Save token use.
        # use this format can save more token than using json.
        dialogue_parts = []
        for msg in messages[-30:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                dialogue_parts.append(f"{role}: {content}")
        dialogue = "\n".join(dialogue_parts)

        catalog_path = self.path / "MEMORY.md"
        try:
            catalog = (
                catalog_path.read_text(encoding="utf-8")
                if catalog_path.exists()
                else ""
            )
        except (OSError, UnicodeError):
            catalog = ""

        prompt = (
            "Extract user preferences, constraints, or project facts.\n"
            "Return JSON array: [{name, type, description, body}].\n"
            "If nothing new or already covered, return [].\n\n"
            f"Existing memories:\n{catalog}\n\nDialogue:\n{dialogue[:4000]}"
        )

        try:
            completion = await self.client.chat.completions.create(
                model=settings.settings.llm_model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.settings.llm_max_tokens,
                temperature=settings.settings.llm_temperature,
            )
            response_text = completion.choices[0].message.content
            memories = (
                _EXTRACTED_MEMORIES_ADAPTER.validate_json(response_text)
                if response_text
                else []
            )
        except Exception:  # noqa: BLE001 - invalid responses must not stop shutdown.
            return

        for memory in memories:
            # write_memory_file also refreshes MEMORY.md after each new memory.
            self.write_memory_file(
                memory.name,
                memory.type,
                memory.description,
                memory.body,
            )


    
    # write memory content into file.
    def write_memory_file(self,name:str, mem_type:str, description:str, body:str):
        self.path.mkdir(parents=True, exist_ok=True)
        slug = name.lower().replace(" ", "-")
        filepath = self.path / f"{slug}.md"
        filepath.write_text(
            f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
        )
        self._rebuild_index()

    # scan the mermory folder. Generate the MEMORY.md according to name and description, given by YAML format.
    def _rebuild_index(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[str, str, str]] = []

        # Sorting by filename keeps numeric indices stable between rebuilds.
        for filepath in sorted(self.path.glob("*.md"), key=lambda path: path.name):
            if filepath.name == "MEMORY.md":
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
                if not text.startswith("---"):
                    continue
                parts = text.split("---", 2)
                metadata = yaml.safe_load(parts[1]) if len(parts) == 3 else None
            except (OSError, UnicodeError, yaml.YAMLError):
                continue

            if not isinstance(metadata, dict):
                continue
            name = metadata.get("name")
            description = metadata.get("description")
            if not isinstance(name, str) or not isinstance(description, str):
                continue
            entries.append((name, filepath.name, description))

        catalog = "".join(
            f"- [{index}]:[{name}]({filename}) — {description}\n"
            for index, (name, filename, description) in enumerate(entries)
        )
        (self.path / "MEMORY.md").write_text(catalog, encoding="utf-8")

    def get_catalog(self) -> str:
        catalog_path = self.path / "MEMORY.md"
        catalog = ""
        try:
            catalog = catalog_path.read_text(encoding='utf-8')
        except Exception:  # noqa: BLE001 - invalid responses must not stop shutdown.
            catalog = ""
        return catalog

    # read the index file MEMORY.md. Ask llm client to choose relevant memories.
    async def select_relevant_memories(self,messages, max_items=5) -> list[str]:

        catalog = self.get_catalog()
        if not catalog.strip():
            return []
        # extract user and assistant dialog
        dialogue_parts = []
        for msg in messages[-30:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role in ("assistant","user","system") and isinstance(content, str) and content.strip():
                dialogue_parts.append(f"{role}: {content}")
        dialogue = "\n".join(dialogue_parts)

        prompt = (f"Select relevant memory indices.\n"
                  f"Return only a JSON array of catalog indices, such as [0, 2].\n"
                  f"Return [] when none are relevant.\n\n"
                  f"Recent conversation:\n{dialogue}\n\nMemory catalog:\n{catalog}")
        try:
            completion = await self.client.chat.completions.create(
                model=settings.settings.llm_model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.settings.llm_max_tokens,
                temperature=settings.settings.llm_temperature,
            )
            response_text = completion.choices[0].message.content
            indices = (
                _SELECTED_MEMORIES_ADAPTER.validate_json(response_text)
                if response_text
                else []
            )
        except Exception:  # noqa: BLE001 - invalid responses must not stop shutdown.
            indices = []

            
        #  extract the content of the indices.
        return self.select_memory_content(indices[:max_items])

    # Read the content of memory, use the index store in MEMORY.md, and find the memory according to file name in MEMORY.md
    def select_memory_content(self, indices : list[int]) -> list[str]:
        import re

        if not indices:
            return []

        try:
            catalog = (self.path / "MEMORY.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return []

        # Map each catalog index to the filename in its Markdown link.
        index_to_filename: dict[int, str] = {}
        entry_pattern = re.compile(r"^- \[(\d+)\]:\[[^]]*\]\(([^)]+)\)")
        for line in catalog.splitlines():
            match = entry_pattern.match(line)
            if match:
                index_to_filename[int(match.group(1))] = match.group(2)

        contents: list[str] = []
        for index in indices:
            filename = index_to_filename.get(index)
            if filename is None:
                continue

            try:
                text = (self.path / filename).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue

            # The body follows the closing delimiter of the YAML frontmatter.
            parts = text.split("---", 2)
            if not text.startswith("---") or len(parts) != 3:
                continue
            contents.append(parts[2].strip())

        return contents
    # TODO : merge old memory files
    def consolidate_memories():
        pass
