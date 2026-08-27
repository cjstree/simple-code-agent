from dataclasses import dataclass


@dataclass
class Entry :
    type : str
    role : "str"
    seq : int
    token_before : int
    token_estimate : int

@dataclass
class MessageEntry(Entry):
    type = "Message"
    content : str # raw message
    replace : str # use for store compacted message, to replace content when construct.

class Session :
    entrys : list[Entry]
    next_seq : int
    total_token : int
    def __init__(self):
        self.entrys = []
        self.next_seq = 0
        self.total_token = 0
    def append_message(self,):
