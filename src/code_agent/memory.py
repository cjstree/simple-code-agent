from pathlib import Path
from openai import OpenAI

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
        dialogue = (messages[-10:])
        existing = "\n".join(f"- {m['name']}: {m['description']}" for m in list_memory_files())

        prompt = (
            "Extract user preferences, constraints, or project facts.\n"
            "Return JSON array: [{name, type, description, body}].\n"
            "If nothing new or already covered, return [].\n\n"
            f"Existing memories:\n{existing}\n\nDialogue:\n{dialogue[:4000]}"
        )
    # TODO parse response, write files ...


    
    # write memory content into file.
    def write_memory_file(self,name:str, mem_type:str, description:str, body:str):
        slug = name.lower().replace(" ", "-")
        filepath = self.path / f"{slug}.md"
        filepath.write_text(
            f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
        )
        self._rebuild_index()

    # scan the mermory folder. Generate the MEMORY.md according to name and description, given by YAML format.
    def _rebuild_index(self) -> None:
        pass

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