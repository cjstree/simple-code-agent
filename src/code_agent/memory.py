from pathlib import Path

import yaml
from openai import OpenAI
from pydantic import BaseModel, TypeAdapter

from code_agent import settings


class ExtractedMemory(BaseModel):
    """A memory item returned by the language model."""

    name: str
    type: str
    description: str
    body: str


_EXTRACTED_MEMORIES_ADAPTER = TypeAdapter(list[ExtractedMemory])

'''
When a new agent loop comes, use select_relevant_memories to learn the what the memory needed by the context.
When agent shundown, call extract_memories.

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
    client : OpenAI
    def __init__(self,path : Path,client : OpenAI) :
        self.path = path
        self.client = client

    # extract memory from current messages.
    def extract_memories(self,messages: list[dict[str,str]]) -> None:
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
            completion = self.client.chat.completions.create(
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

    def _get_catalog(self) -> str:
        catalog_path = self.path / "MEMORY.md"
        catalog = catalog_path.read_text(encoding='utf-8')
        if not catalog:
            return ""

    # read the index file MEMORY.md. Ask llm client to choose relevant memories.
    def select_relevant_memories(self,messages, max_items=5) -> list[str]:
        catalog_path = self.path / "MEMORY.md"
        catalog = catalog_path.read_text(encoding='utf-8')
        if not catalog:
            return []


        response = self.client.messages.create(model=MODEL, messages=[{"role": "user",
            "content": f"Select relevant memory indices. Return JSON array.\n\n"
                    f"Recent conversation:\n{recent}\n\nMemory catalog:\n{catalog}"}],
            max_tokens=200)
        text = extract_text(response.content).strip()
        indices = json.loads(re.search(r'\[.*?\]', text).group())
        #  TODO extract the content of the indices.
        return [files[i]["filename"] for i in indices if 0 <= i < len(files)]
